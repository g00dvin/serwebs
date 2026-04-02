"""Telnet gateway — maps Telnet sessions to serial ports.

Users connect via: telnet host 2323
The gateway authenticates with username/password prompt,
then presents a menu to select an open serial port.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

logger = logging.getLogger("serwebs.telnet")

_telnet_server: Optional[asyncio.AbstractServer] = None


async def start_telnet_gateway(host: str = "0.0.0.0", port: int = 2323,
                                timeout: int = 120) -> None:
    global _telnet_server

    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        logger.info("Telnet connection from %s", peer)

        try:
            await _telnet_session(reader, writer, timeout)
        except Exception as e:
            logger.debug("Telnet session error for %s: %s", peer, e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            logger.info("Telnet connection closed: %s", peer)

    try:
        _telnet_server = await asyncio.start_server(handle_client, host, port)
        logger.info("Telnet gateway started on %s:%d", host, port)
    except Exception as e:
        logger.error("Failed to start Telnet gateway: %s", e)


async def _telnet_session(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                           timeout: int) -> None:
    from serwebs.auth import authenticate_user
    from serwebs.app import get_port_manager, get_ws_manager, get_audit_logger

    # Authentication
    writer.write(b"\r\nSerWebs Telnet Gateway\r\n\r\n")
    writer.write(b"Username: ")
    await writer.drain()

    try:
        username = await asyncio.wait_for(_read_line(reader, writer), timeout=30)
    except asyncio.TimeoutError:
        writer.write(b"\r\nTimeout.\r\n")
        await writer.drain()
        return

    writer.write(b"Password: ")
    await writer.drain()

    try:
        password = await asyncio.wait_for(_read_line(reader, writer, echo=False), timeout=30)
    except asyncio.TimeoutError:
        writer.write(b"\r\nTimeout.\r\n")
        await writer.drain()
        return

    user = authenticate_user(username, password)
    if not user:
        get_audit_logger().log("telnet_login_failed", user=username)
        writer.write(b"\r\nAuthentication failed.\r\n")
        await writer.drain()
        return

    username = user["username"]
    role = user["role"]
    is_viewer = role == "viewer"
    get_audit_logger().log("telnet_login", user=username, details={"role": role})

    writer.write(f"\r\nWelcome to SerWebs Telnet Gateway, {username}!\r\n".encode())
    writer.write(f"Role: {role}\r\n\r\n".encode())
    await writer.drain()

    pm = get_port_manager()

    while True:
        # Show open ports
        pm.scan_ports()
        ports = [p for p in pm.get_ports() if p.status.value == "open"]
        if not ports:
            writer.write(b"No ports are currently open. Waiting...\r\n")
            await writer.drain()
            await asyncio.sleep(3)
            continue

        writer.write(b"Available ports:\r\n")
        for i, p in enumerate(ports, 1):
            name = p.alias or p.id
            desc = f" ({p.description})" if p.description else ""
            baud = f" [{p.settings.baudrate} baud]" if p.settings else ""
            writer.write(f"  {i}. {name}{desc}{baud}\r\n".encode())
        writer.write(b"  q. Quit\r\n\r\n")
        writer.write(b"Select port: ")
        await writer.drain()

        try:
            line = await asyncio.wait_for(_read_line(reader, writer), timeout=timeout)
        except asyncio.TimeoutError:
            writer.write(b"\r\nSession timed out.\r\n")
            await writer.drain()
            break

        line = line.strip()
        if line.lower() == "q":
            break

        try:
            idx = int(line) - 1
            if 0 <= idx < len(ports):
                selected = ports[idx]
            else:
                writer.write(b"Invalid selection.\r\n\r\n")
                await writer.drain()
                continue
        except ValueError:
            writer.write(b"Invalid input.\r\n\r\n")
            await writer.drain()
            continue

        # Bridge telnet <-> serial
        await _bridge_serial_telnet(reader, writer, selected.id, username, is_viewer, pm)
        writer.write(b"\r\nDisconnected from port.\r\n\r\n")
        await writer.drain()

    writer.write(b"Goodbye!\r\n")
    await writer.drain()


async def _read_line(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                      echo: bool = True) -> str:
    """Read a line from telnet client with optional echo."""
    buf = bytearray()
    while True:
        data = await reader.read(1)
        if not data:
            return ""
        ch = data[0]

        # Handle telnet IAC commands (skip them)
        if ch == 255:  # IAC
            cmd = await reader.read(1)
            if cmd and cmd[0] in (251, 252, 253, 254):  # WILL/WONT/DO/DONT
                await reader.read(1)  # option byte
            continue

        if ch in (13, 10):  # CR or LF
            # Consume trailing LF after CR
            if ch == 13:
                try:
                    next_byte = await asyncio.wait_for(reader.read(1), timeout=0.1)
                except asyncio.TimeoutError:
                    pass
            writer.write(b"\r\n")
            await writer.drain()
            return buf.decode("utf-8", errors="replace")

        if ch in (127, 8):  # Backspace/DEL
            if buf:
                buf.pop()
                if echo:
                    writer.write(b"\x08 \x08")
                    await writer.drain()
            continue

        if ch == 3:  # Ctrl+C
            return "q"

        buf.append(ch)
        if echo:
            writer.write(bytes([ch]))
            await writer.drain()


async def _bridge_serial_telnet(reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                                 port_id: str, username: str, is_viewer: bool, pm) -> None:
    """Bridge Telnet I/O with a serial port."""
    from serwebs.app import get_ws_manager, get_audit_logger

    worker = pm.get_worker(port_id)
    if not worker or not worker.is_running:
        writer.write(b"Port is no longer available.\r\n")
        await writer.drain()
        return

    get_audit_logger().log("telnet_connect", user=username, port_id=port_id)
    writer.write(f"\r\nConnected to {port_id}. Press Ctrl+] to disconnect.\r\n\r\n".encode())
    await writer.drain()

    wsm = get_ws_manager()
    rx_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1000)

    # Replay ring buffer
    buf = wsm._get_buffer(port_id)
    replay = buf.read_all()
    if replay:
        writer.write(replay)
        await writer.drain()

    running = True

    async def rx_task():
        """Forward serial RX to telnet."""
        original_broadcast = wsm.broadcast.__func__  # type: ignore[attr-defined]

        async def _hooked_broadcast(self_wsm, pid, data):
            if pid == port_id and running:
                try:
                    rx_queue.put_nowait(data)
                except asyncio.QueueFull:
                    pass
            await original_broadcast(self_wsm, pid, data)

        wsm.broadcast = _hooked_broadcast.__get__(wsm, type(wsm))  # type: ignore[attr-defined]
        try:
            while running:
                try:
                    data = await asyncio.wait_for(rx_queue.get(), timeout=1.0)
                    writer.write(data)
                    await writer.drain()
                except asyncio.TimeoutError:
                    if not worker.is_running:
                        writer.write(b"\r\n[Port disconnected]\r\n")
                        await writer.drain()
                        break
        finally:
            wsm.broadcast = original_broadcast.__get__(wsm, type(wsm))  # type: ignore[attr-defined]

    async def tx_task():
        """Forward telnet input to serial."""
        nonlocal running
        while running:
            try:
                data = await asyncio.wait_for(reader.read(1024), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if not data:
                running = False
                break
            # Check for Ctrl+]
            if b"\x1d" in data:
                running = False
                break
            # Strip telnet IAC sequences
            clean = bytearray()
            i = 0
            while i < len(data):
                if data[i] == 255 and i + 1 < len(data):
                    if data[i + 1] in (251, 252, 253, 254) and i + 2 < len(data):
                        i += 3
                    else:
                        i += 2
                    continue
                clean.append(data[i])
                i += 1
            if clean and not is_viewer:
                await worker.write(bytes(clean))

    rx = asyncio.create_task(rx_task())
    tx = asyncio.create_task(tx_task())
    try:
        await asyncio.gather(rx, tx, return_exceptions=True)
    finally:
        running = False
        rx.cancel()
        tx.cancel()
        get_audit_logger().log("telnet_disconnect", user=username, port_id=port_id)


async def stop_telnet_gateway() -> None:
    global _telnet_server
    if _telnet_server:
        _telnet_server.close()
        await _telnet_server.wait_closed()
        _telnet_server = None
        logger.info("Telnet gateway stopped")
