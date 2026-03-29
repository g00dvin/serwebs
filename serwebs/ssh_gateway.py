"""SSH/Telnet gateway — maps SSH sessions to serial ports.

Requires: pip install asyncssh

Usage: enabled via config.toml [ssh] section.
Users connect via: ssh -p 2222 user@host
The gateway authenticates using the same user database as the web UI,
then presents a menu to select an open serial port.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("serwebs.ssh")

_ssh_server: Optional[asyncio.AbstractServer] = None


async def start_ssh_gateway(host: str = "0.0.0.0", port: int = 2222,
                            host_key_file: str = "") -> None:
    global _ssh_server
    try:
        import asyncssh
    except ImportError:
        logger.warning("asyncssh not installed — SSH gateway disabled. Install with: pip install asyncssh")
        return

    from serwebs.auth import authenticate_user
    from serwebs.config import get_config, get_config_dir

    cfg = get_config()

    # Generate or load host key
    if host_key_file and Path(host_key_file).exists():
        key_path = Path(host_key_file)
    else:
        key_path = get_config_dir() / "ssh_host_key"
        if not key_path.exists():
            logger.info("Generating SSH host key at %s", key_path)
            key = asyncssh.generate_private_key("ssh-rsa", 2048)
            key.write_private_key(str(key_path))
            key.write_public_key(str(key_path) + ".pub")

    class SerWebsSSHServer(asyncssh.SSHServer):
        def connection_made(self, conn):
            self._conn = conn
            self._user = None
            self._role = None
            logger.info("SSH connection from %s", conn.get_extra_info("peername"))

        def connection_lost(self, exc):
            logger.info("SSH connection closed")

        def begin_auth(self, username):
            return True  # Require auth

        def password_auth_supported(self):
            return True

        def validate_password(self, username, password):
            user = authenticate_user(username, password)
            if user:
                self._user = user["username"]
                self._role = user["role"]
                return True
            return False

    async def handle_session(process):
        """Handle an SSH session — present port selection menu and bridge I/O."""
        from serwebs.app import get_port_manager, get_ws_manager

        pm = get_port_manager()
        server = process.channel.get_connection().get_owner()
        username = getattr(server, "_user", "unknown")
        role = getattr(server, "_role", "viewer")
        is_viewer = role == "viewer"

        process.stdout.write(f"\r\nWelcome to SerWebs SSH Gateway, {username}!\r\n")
        process.stdout.write(f"Role: {role}\r\n\r\n")

        while True:
            # Show open ports
            pm.scan_ports()
            ports = [p for p in pm.get_ports() if p.status.value == "open"]
            if not ports:
                process.stdout.write("No ports are currently open. Waiting...\r\n")
                await asyncio.sleep(3)
                continue

            process.stdout.write("Available ports:\r\n")
            for i, p in enumerate(ports, 1):
                name = p.alias or p.id
                desc = f" ({p.description})" if p.description else ""
                baud = f" [{p.settings.baudrate} baud]" if p.settings else ""
                process.stdout.write(f"  {i}. {name}{desc}{baud}\r\n")
            process.stdout.write(f"  q. Quit\r\n\r\n")
            process.stdout.write("Select port: ")

            try:
                line = await asyncio.wait_for(_read_line(process), timeout=120)
            except asyncio.TimeoutError:
                process.stdout.write("\r\nSession timed out.\r\n")
                break

            line = line.strip()
            if line.lower() == "q":
                break

            try:
                idx = int(line) - 1
                if 0 <= idx < len(ports):
                    selected = ports[idx]
                else:
                    process.stdout.write("Invalid selection.\r\n\r\n")
                    continue
            except ValueError:
                process.stdout.write("Invalid input.\r\n\r\n")
                continue

            # Bridge SSH <-> serial port
            await _bridge_serial(process, selected.id, username, is_viewer, pm)
            process.stdout.write("\r\nDisconnected from port.\r\n\r\n")

        process.stdout.write("Goodbye!\r\n")
        process.exit(0)

    async def _read_line(process) -> str:
        """Read a line from SSH stdin with echo."""
        buf = []
        while True:
            data = await process.stdin.read(1)
            if not data:
                return ""
            ch = data
            if ch in ("\r", "\n"):
                process.stdout.write("\r\n")
                return "".join(buf)
            if ch in ("\x7f", "\x08"):  # backspace
                if buf:
                    buf.pop()
                    process.stdout.write("\x08 \x08")
                continue
            if ch == "\x03":  # Ctrl+C
                return "q"
            buf.append(ch)
            process.stdout.write(ch)

    async def _bridge_serial(process, port_id: str, username: str, is_viewer: bool, pm):
        """Bridge SSH I/O with a serial port."""
        from serwebs.app import get_audit_logger

        worker = pm.get_worker(port_id)
        if not worker or not worker.is_running:
            process.stdout.write("Port is no longer available.\r\n")
            return

        get_audit_logger().log("ssh_connect", user=username, port_id=port_id)
        process.stdout.write(f"\r\nConnected to {port_id}. Press Ctrl+] to disconnect.\r\n\r\n")

        # RX: serial -> SSH
        rx_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1000)

        def on_rx(data: bytes):
            try:
                rx_queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

        # Hook into ws_manager's broadcast (we use a callback approach)
        from serwebs.app import get_ws_manager
        wsm = get_ws_manager()

        # We'll poll the ring buffer for initial replay, then use a task
        buf = wsm._get_buffer(port_id)
        replay = buf.read_all()
        if replay:
            process.stdout.write(replay.decode("utf-8", errors="replace"))

        running = True

        async def rx_task():
            """Forward serial RX to SSH."""
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
                        process.stdout.write(data.decode("utf-8", errors="replace"))
                    except asyncio.TimeoutError:
                        if not worker.is_running:
                            process.stdout.write("\r\n[Port disconnected]\r\n")
                            break
            finally:
                wsm.broadcast = original_broadcast.__get__(wsm, type(wsm))  # type: ignore[attr-defined]

        async def tx_task():
            """Forward SSH input to serial."""
            nonlocal running
            while running:
                try:
                    data = await asyncio.wait_for(process.stdin.read(1024), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    running = False
                    break
                # Check for Ctrl+]
                if "\x1d" in data:
                    running = False
                    break
                if not is_viewer:
                    await worker.write(data.encode("utf-8") if isinstance(data, str) else data)

        rx = asyncio.create_task(rx_task())
        tx = asyncio.create_task(tx_task())
        try:
            await asyncio.gather(rx, tx, return_exceptions=True)
        finally:
            running = False
            rx.cancel()
            tx.cancel()
            get_audit_logger().log("ssh_disconnect", user=username, port_id=port_id)

    try:
        _ssh_server = await asyncssh.create_server(
            SerWebsSSHServer,
            host=host,
            port=port,
            server_host_keys=[str(key_path)],
            process_factory=handle_session,
        )
        logger.info("SSH gateway started on %s:%d", host, port)
    except Exception as e:
        logger.error("Failed to start SSH gateway: %s", e)


async def stop_ssh_gateway() -> None:
    global _ssh_server
    if _ssh_server:
        _ssh_server.close()
        await _ssh_server.wait_closed()
        _ssh_server = None
        logger.info("SSH gateway stopped")
