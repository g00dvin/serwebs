from __future__ import annotations

import base64
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from fastapi import WebSocket

from serwebs.config import get_config
from serwebs.utils import RingBuffer

if TYPE_CHECKING:
    from serwebs.recording import Recorder
    from serwebs.session_logger import SessionLogger

logger = logging.getLogger("serwebs.ws")


class WsManager:
    """Manages WebSocket connections grouped by port, with ring buffer replay."""

    def __init__(self, session_logger: Optional["SessionLogger"] = None,
                 recorder: Optional["Recorder"] = None):
        self._connections: dict[str, list[WebSocket]] = {}
        self._ring_buffers: dict[str, RingBuffer] = {}
        self._session_logger = session_logger
        self._recorder = recorder

    def _get_buffer(self, port_id: str) -> RingBuffer:
        if port_id not in self._ring_buffers:
            cfg = get_config().serial
            self._ring_buffers[port_id] = RingBuffer(cfg.ring_buffer_size)
        return self._ring_buffers[port_id]

    async def connect(self, port_id: str, ws: WebSocket) -> None:
        await ws.accept()
        if port_id not in self._connections:
            self._connections[port_id] = []
        self._connections[port_id].append(ws)

        # Send ring buffer replay
        buf = self._get_buffer(port_id)
        replay_data = buf.read_all()
        if replay_data:
            msg = {
                "type": "replay",
                "payload": base64.b64encode(replay_data).decode(),
            }
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                pass

        # Send status
        await self._send_to(ws, {"type": "status", "state": "connected"})
        logger.info("Client connected to port %s (total: %d)", port_id, len(self._connections[port_id]))

    async def disconnect(self, port_id: str, ws: WebSocket) -> int:
        """Remove a WebSocket. Returns remaining client count for this port."""
        clients = self._connections.get(port_id, [])
        if ws in clients:
            clients.remove(ws)
        remaining = len(clients)
        if remaining == 0:
            self._connections.pop(port_id, None)
        logger.info("Client disconnected from port %s (remaining: %d)", port_id, remaining)
        return remaining

    def broadcast_sync(self, port_id: str, data: bytes) -> None:
        """Called from serial worker callback (synchronous context).
        Schedules async broadcast via the event loop.
        """
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcast(port_id, data))
        except RuntimeError:
            pass

    async def broadcast(self, port_id: str, data: bytes) -> None:
        """Send serial data to all connected clients for a port."""
        buf = self._get_buffer(port_id)
        buf.append(data)

        # Log RX data to session log
        if self._session_logger:
            self._session_logger.log_data(port_id, "rx", data)

        # Record RX data if recording is active
        if self._recorder and self._recorder.is_recording(port_id):
            self._recorder.record_data(port_id, data, "o")

        clients = self._connections.get(port_id, [])
        if not clients:
            return

        msg = json.dumps({
            "type": "data",
            "payload": base64.b64encode(data).decode(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)

        for ws in dead:
            if ws in clients:
                clients.remove(ws)

    async def broadcast_status(self, port_id: str, state: str) -> None:
        clients = self._connections.get(port_id, [])
        msg = {"type": "status", "state": state}
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in clients:
                clients.remove(ws)

    async def broadcast_error(self, port_id: str, message: str) -> None:
        clients = self._connections.get(port_id, [])
        msg = {"type": "error", "message": message}
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(json.dumps(msg))
            except Exception:
                dead.append(ws)
        for ws in dead:
            if ws in clients:
                clients.remove(ws)

    async def _send_to(self, ws: WebSocket, msg: dict) -> None:
        try:
            await ws.send_text(json.dumps(msg))
        except Exception:
            pass

    def client_count(self, port_id: str) -> int:
        return len(self._connections.get(port_id, []))

    def total_clients(self) -> int:
        return sum(len(c) for c in self._connections.values())

    def clear_buffer(self, port_id: str) -> None:
        if port_id in self._ring_buffers:
            self._ring_buffers[port_id].clear()

    async def disconnect_all(self, port_id: str) -> None:
        """Close all WebSocket connections for a port."""
        clients = self._connections.pop(port_id, [])
        for ws in clients:
            try:
                await ws.close(code=1001, reason="Port closed")
            except Exception:
                pass

    async def shutdown(self) -> None:
        """Close all connections on all ports."""
        for port_id in list(self._connections.keys()):
            await self.disconnect_all(port_id)
        self._ring_buffers.clear()
