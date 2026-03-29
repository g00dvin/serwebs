from __future__ import annotations

import asyncio
import logging
from typing import Callable, Optional

import serial
import serial_asyncio

from serwebs.models import Parity, FlowControl, PortSettings

logger = logging.getLogger("serwebs.serial")


class SerialWorker:
    """Manages async read/write for a single serial port."""

    def __init__(
        self,
        device: str,
        settings: PortSettings,
        on_data: Callable[[bytes], None],
        on_error: Callable[[str], None],
    ):
        self.device = device
        self.settings = settings
        self._on_data = on_data
        self._on_error = on_error
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._write_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1000)
        self._read_task: Optional[asyncio.Task] = None
        self._write_task: Optional[asyncio.Task] = None
        self._running = False

    def _get_parity(self) -> str:
        return {
            Parity.NONE: serial.PARITY_NONE,
            Parity.EVEN: serial.PARITY_EVEN,
            Parity.ODD: serial.PARITY_ODD,
        }[self.settings.parity]

    async def start(self) -> None:
        parity = self._get_parity()
        xonxoff = self.settings.flowcontrol == FlowControl.XONXOFF
        rtscts = self.settings.flowcontrol == FlowControl.RTSCTS

        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self.device,
                baudrate=self.settings.baudrate,
                bytesize=self.settings.databits,
                parity=parity,
                stopbits=self.settings.stopbits,
                xonxoff=xonxoff,
                rtscts=rtscts,
                timeout=self.settings.read_timeout,
                write_timeout=self.settings.write_timeout,
            )
        except (serial.SerialException, OSError) as e:
            logger.error("Failed to open %s: %s", self.device, e)
            self._on_error(f"Failed to open port: {e}")
            raise

        self._running = True
        self._read_task = asyncio.create_task(self._read_loop(), name=f"serial-read-{self.device}")
        self._write_task = asyncio.create_task(self._write_loop(), name=f"serial-write-{self.device}")
        logger.info("Serial worker started for %s @ %d baud", self.device, self.settings.baudrate)

    async def _read_loop(self) -> None:
        try:
            while self._running and self._reader:
                data = await self._reader.read(4096)
                if not data:
                    await asyncio.sleep(0.01)
                    continue
                self._on_data(data)
        except (serial.SerialException, OSError) as e:
            if self._running:
                logger.error("Read error on %s: %s", self.device, e)
                self._on_error(f"Device error: {e}")
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    async def _write_loop(self) -> None:
        try:
            while self._running and self._writer:
                data = await self._write_queue.get()
                try:
                    self._writer.write(data)
                    await self._writer.drain()
                except (serial.SerialException, OSError) as e:
                    if self._running:
                        logger.error("Write error on %s: %s", self.device, e)
                        self._on_error(f"Write error: {e}")
                        break
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    async def write(self, data: bytes) -> None:
        if not self._running:
            raise RuntimeError("Worker not running")
        await self._write_queue.put(data)

    async def stop(self) -> None:
        self._running = False
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        if self._write_task and not self._write_task.done():
            self._write_task.cancel()
            try:
                await self._write_task
            except asyncio.CancelledError:
                pass
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
            self._writer = None
        self._reader = None
        logger.info("Serial worker stopped for %s", self.device)

    @property
    def is_running(self) -> bool:
        return self._running
