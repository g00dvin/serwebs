from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from serwebs.auth import try_decode_any_token
from serwebs.config import get_config
from serwebs.utils import RateLimiter

def _get_audit_logger():
    from serwebs.app import get_audit_logger
    return get_audit_logger()

def _get_session_logger():
    from serwebs.app import get_session_logger
    return get_session_logger()

logger = logging.getLogger("serwebs.ws")
router = APIRouter()
_rate_limiter = RateLimiter()


def _get_port_manager():
    from serwebs.app import get_port_manager
    return get_port_manager()


def _get_ws_manager():
    from serwebs.app import get_ws_manager
    return get_ws_manager()


def _authenticate_ws(token: str | None) -> dict | None:
    if not token:
        return None
    return try_decode_any_token(token)


@router.websocket("/ws/{port_id}")
async def websocket_endpoint(ws: WebSocket, port_id: str, token: str | None = None):
    import asyncio

    # --- Origin validation ---
    origin = ws.headers.get("origin", "")
    request_host = ws.headers.get("host", "")
    if origin:
        # Allow same-origin and localhost variants
        from urllib.parse import urlparse
        parsed = urlparse(origin)
        origin_host = parsed.hostname or ""
        origin_port = parsed.port
        req_host, _, req_port = request_host.partition(":")
        # Accept if origin host matches request host (same-origin)
        same_origin = (origin_host == req_host) or (
            origin_host in ("localhost", "127.0.0.1", "::1") and
            req_host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
        )
        if not same_origin:
            logger.warning("WS rejected: origin %s does not match host %s", origin, request_host)
            await ws.close(code=4001, reason="Origin not allowed")
            return

    # --- Authentication ---
    # Method 1: token in query param (used by remote backend connections)
    user = _authenticate_ws(token)

    if not user:
        # Method 2: accept connection, wait for auth message (local browser connections)
        await ws.accept()
        try:
            raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
            msg = json.loads(raw)
            if msg.get("type") == "auth" and msg.get("token"):
                user = _authenticate_ws(msg["token"])
        except (asyncio.TimeoutError, json.JSONDecodeError, Exception):
            pass

        if not user:
            logger.debug("WS auth failed for port %s — no valid token", port_id)
            await ws.send_text(json.dumps({"type": "error", "message": "Authentication required"}))
            await ws.close(code=4001, reason="Authentication required")
            return

    logger.debug("WS auth OK: user=%s, role=%s, port=%s", user["username"], user["role"], port_id)

    pm = _get_port_manager()
    wsm = _get_ws_manager()
    cfg = get_config().serial

    # Check port is open
    worker = pm.get_worker(port_id)
    if not worker:
        logger.debug("WS rejected: port %s is not open", port_id)
        await ws.close(code=4002, reason="Port not open")
        return

    # Check client limit
    current_count = wsm.client_count(port_id)
    if current_count >= cfg.max_clients_per_port:
        logger.debug("WS rejected: port %s has %d clients (max %d)", port_id, current_count, cfg.max_clients_per_port)
        await ws.close(code=4003, reason="Client limit reached")
        return

    # Viewer role: read-only flag
    is_viewer = user.get("role") == "viewer"

    # Connect
    logger.debug("WS connecting user=%s to port=%s (existing clients: %d)", user["username"], port_id, current_count)
    await wsm.connect(port_id, ws)
    client_key = f"{user['username']}:{id(ws)}"
    logger.info("WS connected: user=%s, port=%s, clients=%d", user["username"], port_id, wsm.client_count(port_id))
    _get_audit_logger().log("ws_connect", user=user["username"], port_id=port_id)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug("WS invalid JSON from %s: %s", user["username"], raw[:100])
                await ws.send_text(json.dumps({"type": "error", "message": "Invalid JSON"}))
                continue

            msg_type = msg.get("type")
            logger.debug("WS msg from %s: type=%s, len=%d", user["username"], msg_type, len(raw))

            if msg_type == "write":
                if is_viewer:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Read-only mode: viewers cannot write to ports",
                    }))
                    continue

                # Check port lock
                from serwebs.config import load_port_locks
                locks = load_port_locks()
                lock_info = locks.get(port_id)
                if lock_info and lock_info.get("user") != user["username"]:
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Port is locked by " + lock_info.get("user", "unknown") + ". Unlock to write.",
                    }))
                    continue

                payload = msg.get("payload", "")
                data = payload.encode("utf-8")

                if len(data) > cfg.max_message_size:
                    logger.debug("WS write rejected: too large (%d > %d)", len(data), cfg.max_message_size)
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": f"Message too large (max {cfg.max_message_size} bytes)",
                    }))
                    continue

                if not _rate_limiter.allow(client_key):
                    logger.debug("WS write rejected: rate limit for %s", user["username"])
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Rate limit exceeded",
                    }))
                    continue

                current_worker = pm.get_worker(port_id)
                if current_worker and current_worker.is_running:
                    await current_worker.write(data)
                    _get_session_logger().log_data(port_id, "tx", data)
                    # Record TX if recording is active
                    from serwebs.recording import get_recorder
                    rec = get_recorder()
                    if rec and rec.is_recording(port_id):
                        rec.record_data(port_id, data, "i")
                    logger.debug("WS wrote %d bytes to %s", len(data), port_id)
                else:
                    logger.warning("WS write failed: worker for %s not running", port_id)
                    await ws.send_text(json.dumps({
                        "type": "error",
                        "message": "Port is no longer available",
                    }))

            elif msg_type == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        logger.debug("WS client disconnected normally: user=%s, port=%s", user["username"], port_id)
    except Exception as e:
        logger.error("WS error for %s on port %s: %s", user["username"], port_id, e, exc_info=True)
    finally:
        _rate_limiter.remove(client_key)
        remaining = await wsm.disconnect(port_id, ws)
        _get_audit_logger().log("ws_disconnect", user=user["username"], port_id=port_id)
        logger.info("WS cleanup: user=%s, port=%s, remaining=%d", user["username"], port_id, remaining)
