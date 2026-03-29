from __future__ import annotations

import asyncio
import fnmatch
import glob
import logging
import os
from typing import Optional

from serwebs.config import get_config, load_port_aliases
from serwebs.models import PortInfo, PortSettings, PortStatus
from serwebs.serial_worker import SerialWorker
from serwebs.ws_manager import WsManager

logger = logging.getLogger("serwebs.ports")


class PortManager:
    """Discovers serial ports, manages workers, handles hot-plug events."""

    def __init__(self, ws_manager: WsManager):
        self.ws = ws_manager
        self._ports: dict[str, PortInfo] = {}
        self._workers: dict[str, SerialWorker] = {}
        self._udev_task: Optional[asyncio.Task] = None

    def scan_ports(self) -> dict[str, PortInfo]:
        """Scan for serial ports matching configured patterns."""
        cfg = get_config().serial
        aliases = load_port_aliases()
        found: dict[str, PortInfo] = {}

        for pattern in cfg.port_patterns:
            for device in sorted(glob.glob(pattern)):
                port_id = os.path.basename(device)
                if any(fnmatch.fnmatch(device, bp) for bp in cfg.blacklist_patterns):
                    continue
                if port_id in self._workers:
                    status = PortStatus.OPEN
                    settings = self._workers[port_id].settings
                    clients = self.ws.client_count(port_id)
                elif self._is_busy(device):
                    status = PortStatus.BUSY
                    settings = None
                    clients = 0
                else:
                    status = PortStatus.FREE
                    settings = None
                    clients = 0
                desc = self._get_description(device)
                found[port_id] = PortInfo(
                    id=port_id,
                    device=device,
                    description=desc,
                    alias=aliases.get(port_id, ""),
                    status=status,
                    settings=settings,
                    clients=clients,
                )

        # Keep open ports that disappeared from scan
        for port_id, worker in list(self._workers.items()):
            if port_id not in found:
                found[port_id] = PortInfo(
                    id=port_id,
                    device=worker.device,
                    alias=aliases.get(port_id, ""),
                    status=PortStatus.UNAVAILABLE,
                    settings=worker.settings,
                    clients=self.ws.client_count(port_id),
                )

        self._ports = found
        return found

    def get_ports(self) -> list[PortInfo]:
        return list(self._ports.values())

    def get_port(self, port_id: str) -> Optional[PortInfo]:
        return self._ports.get(port_id)

    async def open_port(self, port_id: str, settings: PortSettings) -> PortInfo:
        cfg = get_config().serial
        if len(self._workers) >= cfg.max_ports:
            raise ValueError(f"Maximum number of open ports ({cfg.max_ports}) reached")
        if port_id in self._workers:
            raise ValueError(f"Port {port_id} is already open")

        port = self._ports.get(port_id)
        if not port:
            self.scan_ports()
            port = self._ports.get(port_id)
        if not port:
            raise ValueError(f"Port {port_id} not found")
        if port.status == PortStatus.BUSY:
            raise ValueError(f"Port {port_id} is busy (used by another process)")

        # Check permissions
        if not os.access(port.device, os.R_OK | os.W_OK):
            raise PermissionError(
                f"No read/write access to {port.device}. "
                "Add user to 'dialout' group: sudo usermod -aG dialout $USER"
            )

        # Apply default timeouts from config if not set
        if settings.read_timeout is None:
            settings.read_timeout = cfg.read_timeout
        if settings.write_timeout is None:
            settings.write_timeout = cfg.write_timeout

        def on_data(data: bytes) -> None:
            self.ws.broadcast_sync(port_id, data)

        def on_error(msg: str) -> None:
            asyncio.get_running_loop().create_task(self._handle_device_error(port_id, msg))

        worker = SerialWorker(
            device=port.device,
            settings=settings,
            on_data=on_data,
            on_error=on_error,
        )
        await worker.start()
        self._workers[port_id] = worker

        port.status = PortStatus.OPEN
        port.settings = settings
        self._ports[port_id] = port
        logger.info("Port %s opened with settings: %s", port_id, settings)
        return port

    async def close_port(self, port_id: str) -> None:
        worker = self._workers.pop(port_id, None)
        if worker:
            await worker.stop()
        await self.ws.broadcast_status(port_id, "disconnected")
        await self.ws.disconnect_all(port_id)
        self.ws.clear_buffer(port_id)

        if port_id in self._ports:
            self._ports[port_id].status = PortStatus.FREE
            self._ports[port_id].settings = None
            self._ports[port_id].clients = 0
        logger.info("Port %s closed", port_id)

    async def _handle_device_error(self, port_id: str, message: str) -> None:
        logger.error("Device error on %s: %s", port_id, message)
        await self.ws.broadcast_status(port_id, "device_lost")
        await self.ws.broadcast_error(port_id, message)
        worker = self._workers.pop(port_id, None)
        if worker:
            await worker.stop()
        if port_id in self._ports:
            self._ports[port_id].status = PortStatus.UNAVAILABLE
        self.scan_ports()

    def get_worker(self, port_id: str) -> Optional[SerialWorker]:
        return self._workers.get(port_id)

    def start_udev_monitor(self) -> None:
        """Start monitoring udev events for USB serial devices."""
        self._udev_task = asyncio.create_task(self._udev_loop(), name="udev-monitor")

    async def _udev_loop(self) -> None:
        try:
            import pyudev
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem="tty")

            def _monitor_thread(queue: asyncio.Queue) -> None:
                for device in iter(monitor.poll, None):
                    try:
                        queue.put_nowait((device.action, device.device_node))
                    except asyncio.QueueFull:
                        pass

            queue: asyncio.Queue = asyncio.Queue(maxsize=100)
            loop = asyncio.get_running_loop()
            loop.run_in_executor(None, _monitor_thread, queue)

            while True:
                action, device_node = await queue.get()
                if device_node:
                    logger.info("udev event: %s %s", action, device_node)
                    port_id = os.path.basename(device_node)
                    if action == "remove" and port_id in self._workers:
                        await self._handle_device_error(port_id, "Device disconnected")
                    self.scan_ports()
        except ImportError:
            logger.warning("pyudev not available, falling back to periodic scan")
            await self._periodic_scan()
        except Exception as e:
            logger.error("udev monitor error: %s", e)
            await self._periodic_scan()

    async def _periodic_scan(self) -> None:
        """Fallback: periodically scan for port changes."""
        while True:
            await asyncio.sleep(3.0)
            self.scan_ports()

    async def shutdown(self) -> None:
        """Gracefully close all ports and stop monitoring."""
        if self._udev_task and not self._udev_task.done():
            self._udev_task.cancel()
            try:
                await self._udev_task
            except asyncio.CancelledError:
                pass
        for port_id in list(self._workers.keys()):
            await self.close_port(port_id)
        logger.info("All ports closed, port manager shut down")

    @staticmethod
    def _is_busy(device: str) -> bool:
        """Check if device is locked by another process (best effort)."""
        lock_file = f"/var/lock/LCK..{os.path.basename(device)}"
        return os.path.exists(lock_file)

    @staticmethod
    def _get_description(device: str) -> str:
        """Try to get a human-readable description of the device."""
        try:
            sys_path = f"/sys/class/tty/{os.path.basename(device)}/device"
            if os.path.islink(sys_path):
                # Try to read product name from parent USB device
                usb_path = os.path.realpath(sys_path)
                product_file = os.path.join(usb_path, "..", "product")
                if os.path.exists(product_file):
                    with open(product_file) as f:
                        return f.read().strip()
                manufacturer_file = os.path.join(usb_path, "..", "manufacturer")
                if os.path.exists(manufacturer_file):
                    with open(manufacturer_file) as f:
                        return f.read().strip()
        except Exception:
            pass
        return ""

    @property
    def open_port_count(self) -> int:
        return len(self._workers)
