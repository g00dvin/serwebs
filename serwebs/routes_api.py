from __future__ import annotations

import asyncio
import base64
import time
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from serwebs import __version__
from serwebs.auth import authenticate_user, create_access_token, get_current_user, require_role
from serwebs.config import (
    get_config,
    set_port_alias,
    load_port_tags,
    set_port_tags,
    get_all_tag_names,
    load_port_profiles,
    set_port_profile,
    delete_port_profile,
)
from serwebs.models import (
    ErrorResponse,
    HealthResponse,
    LoginRequest,
    MetricsResponse,
    PortInfo,
    PortOpenRequest,
    PortRenameRequest,
    PortStatus,
    TokenResponse,
)

router = APIRouter()
_start_time = time.monotonic()


def _get_port_manager():
    from serwebs.app import get_port_manager
    return get_port_manager()


def _get_ws_manager():
    from serwebs.app import get_ws_manager
    return get_ws_manager()


def _get_audit_logger():
    from serwebs.app import get_audit_logger
    return get_audit_logger()


def _get_session_logger():
    from serwebs.app import get_session_logger
    return get_session_logger()


# ─── Auth ───

@router.get("/auth/config")
async def auth_config():
    """Return auth configuration for the frontend (OIDC enabled, authorize URL, etc)."""
    cfg = get_config().auth
    oidc = cfg.oidc
    result = {"local_auth": len(cfg.users) > 0}
    if oidc.enabled and oidc.issuer and oidc.client_id:
        result["oidc"] = {
            "enabled": True,
            "authorize_url": oidc.issuer.rstrip("/") + "/authorize/",
            "client_id": oidc.client_id,
        }
    return result


@router.post("/auth/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    user = authenticate_user(req.username, req.password)
    if not user:
        _get_audit_logger().log("login_failed", user=req.username)
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token(user["username"], user["role"])
    _get_audit_logger().log("login", user=user["username"], details={"role": user["role"]})
    return TokenResponse(access_token=token)


@router.post("/auth/oidc/exchange", response_model=TokenResponse)
async def oidc_exchange(request: Request):
    """Exchange an OIDC access/id token for a local SerWebs JWT."""
    from serwebs.auth import _validate_oidc_token
    body = await request.json()
    oidc_token = body.get("token", "")
    if not oidc_token:
        raise HTTPException(status_code=400, detail="Missing token")
    user = _validate_oidc_token(oidc_token)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid OIDC token")
    local_token = create_access_token(user["username"], user["role"])
    _get_audit_logger().log("oidc_login", user=user["username"], details={"role": user["role"]})
    return TokenResponse(access_token=local_token)


# ─── Ports ───

@router.get("/api/ports", response_model=List[PortInfo])
async def list_ports(user: dict = Depends(get_current_user)):
    pm = _get_port_manager()
    pm.scan_ports()
    ports = pm.get_ports()
    # Enrich with tags
    all_tags = load_port_tags()
    for p in ports:
        p.tags = all_tags.get(p.id, [])
    return ports


@router.get("/api/ports/{port_id}", response_model=PortInfo)
async def get_port(port_id: str, user: dict = Depends(get_current_user)):
    pm = _get_port_manager()
    port = pm.get_port(port_id)
    if not port:
        raise HTTPException(status_code=404, detail=f"Port {port_id} not found")
    all_tags = load_port_tags()
    port.tags = all_tags.get(port_id, [])
    return port


@router.post("/api/ports/{port_id}/open", response_model=PortInfo)
async def open_port(port_id: str, req: PortOpenRequest, user: dict = Depends(require_role("admin"))):
    pm = _get_port_manager()
    try:
        result = await pm.open_port(port_id, req.settings)
        _get_audit_logger().log("port_open", user=user["username"], port_id=port_id,
                                details={"baudrate": req.settings.baudrate})
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))


@router.post("/api/ports/{port_id}/close")
async def close_port(port_id: str, user: dict = Depends(require_role("admin"))):
    pm = _get_port_manager()
    if not pm.get_worker(port_id):
        raise HTTPException(status_code=400, detail=f"Port {port_id} is not open")
    await pm.close_port(port_id)
    _get_audit_logger().log("port_close", user=user["username"], port_id=port_id)
    return {"status": "closed"}


@router.get("/api/ports/{port_id}/status")
async def port_status(port_id: str, user: dict = Depends(get_current_user)):
    pm = _get_port_manager()
    port = pm.get_port(port_id)
    if not port:
        raise HTTPException(status_code=404, detail=f"Port {port_id} not found")
    ws = _get_ws_manager()
    return {
        "id": port.id,
        "status": port.status,
        "clients": ws.client_count(port_id),
    }


@router.post("/api/ports/{port_id}/rename")
async def rename_port(port_id: str, req: PortRenameRequest, user: dict = Depends(require_role("admin"))):
    pm = _get_port_manager()
    port = pm.get_port(port_id)
    if not port:
        raise HTTPException(status_code=404, detail=f"Port {port_id} not found")
    set_port_alias(port_id, req.alias)
    _get_audit_logger().log("port_rename", user=user["username"], port_id=port_id,
                            details={"alias": req.alias})
    pm.scan_ports()
    return {"id": port_id, "alias": req.alias}


# ─── Port Tags ───

@router.get("/api/tags")
async def list_all_tags(user: dict = Depends(get_current_user)):
    return {"tags": get_all_tag_names()}


@router.get("/api/ports/{port_id}/tags")
async def get_port_tags(port_id: str, user: dict = Depends(get_current_user)):
    tags = load_port_tags()
    return {"port_id": port_id, "tags": tags.get(port_id, [])}


@router.put("/api/ports/{port_id}/tags")
async def update_port_tags(port_id: str, request: Request, user: dict = Depends(require_role("admin"))):
    body = await request.json()
    tags = body.get("tags", [])
    if not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="tags must be a list of strings")
    all_tags = set_port_tags(port_id, tags)
    _get_audit_logger().log("port_tags_update", user=user["username"], port_id=port_id,
                            details={"tags": all_tags.get(port_id, [])})
    return {"port_id": port_id, "tags": all_tags.get(port_id, [])}


# ─── Port Profiles (Device Config) ───

@router.get("/api/profiles")
async def list_profiles(user: dict = Depends(get_current_user)):
    return {"profiles": load_port_profiles()}


@router.get("/api/profiles/{port_id}")
async def get_profile(port_id: str, user: dict = Depends(get_current_user)):
    profiles = load_port_profiles()
    profile = profiles.get(port_id)
    if not profile:
        raise HTTPException(status_code=404, detail=f"No profile for {port_id}")
    return {"port_id": port_id, "profile": profile}


@router.put("/api/profiles/{port_id}")
async def save_profile(port_id: str, request: Request, user: dict = Depends(require_role("admin"))):
    body = await request.json()
    profile = body.get("profile", body)
    # Validate it has at least baudrate
    if "baudrate" not in profile:
        raise HTTPException(status_code=400, detail="Profile must include baudrate")
    set_port_profile(port_id, profile)
    _get_audit_logger().log("profile_save", user=user["username"], port_id=port_id,
                            details={"baudrate": profile.get("baudrate")})
    return {"port_id": port_id, "profile": profile}


@router.delete("/api/profiles/{port_id}")
async def remove_profile(port_id: str, user: dict = Depends(require_role("admin"))):
    delete_port_profile(port_id)
    _get_audit_logger().log("profile_delete", user=user["username"], port_id=port_id)
    return {"status": "deleted"}


# ─── REST API for Automation ───

@router.post("/api/ports/{port_id}/write")
async def rest_write(port_id: str, request: Request, user: dict = Depends(require_role("user"))):
    """Write data to a serial port via REST API.
    Body: {"data": "string"} or {"data_b64": "base64encoded"}
    """
    if user.get("role") == "viewer":
        raise HTTPException(status_code=403, detail="Viewers cannot write to ports")
    pm = _get_port_manager()
    worker = pm.get_worker(port_id)
    if not worker or not worker.is_running:
        raise HTTPException(status_code=400, detail=f"Port {port_id} is not open")

    body = await request.json()
    data_str = body.get("data")
    data_b64 = body.get("data_b64")
    if data_b64:
        try:
            data = base64.b64decode(data_b64)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid base64 data")
    elif data_str is not None:
        data = data_str.encode("utf-8")
    else:
        raise HTTPException(status_code=400, detail="Provide 'data' or 'data_b64'")

    cfg = get_config().serial
    if len(data) > cfg.max_message_size:
        raise HTTPException(status_code=400, detail=f"Data too large (max {cfg.max_message_size} bytes)")

    await worker.write(data)
    _get_session_logger().log_data(port_id, "tx", data)
    _get_audit_logger().log("rest_write", user=user["username"], port_id=port_id,
                            details={"bytes": len(data)})
    return {"status": "ok", "bytes_written": len(data)}


@router.post("/api/ports/{port_id}/write-wait")
async def rest_write_wait(
    port_id: str,
    request: Request,
    user: dict = Depends(require_role("user")),
):
    """Write data and wait for response. Returns data received within timeout.
    Body: {"data": "string", "timeout": 2.0, "max_bytes": 4096}
    """
    if user.get("role") == "viewer":
        raise HTTPException(status_code=403, detail="Viewers cannot write to ports")
    pm = _get_port_manager()
    worker = pm.get_worker(port_id)
    if not worker or not worker.is_running:
        raise HTTPException(status_code=400, detail=f"Port {port_id} is not open")

    body = await request.json()
    data_str = body.get("data", "")
    timeout = min(body.get("timeout", 2.0), 30.0)
    max_bytes = min(body.get("max_bytes", 4096), 65536)

    data = data_str.encode("utf-8")
    cfg = get_config().serial
    if len(data) > cfg.max_message_size:
        raise HTTPException(status_code=400, detail=f"Data too large (max {cfg.max_message_size} bytes)")

    # Collect response
    collected = bytearray()
    event = asyncio.Event()

    wsm = _get_ws_manager()
    original_broadcast = wsm.broadcast.__func__  # type: ignore[attr-defined]

    async def _capture_broadcast(self_wsm, pid: str, rx_data: bytes):
        if pid == port_id:
            collected.extend(rx_data)
            if len(collected) >= max_bytes:
                event.set()
        await original_broadcast(self_wsm, pid, rx_data)

    # Monkey-patch temporarily
    wsm.broadcast = _capture_broadcast.__get__(wsm, type(wsm))  # type: ignore[attr-defined]
    try:
        await worker.write(data)
        _get_session_logger().log_data(port_id, "tx", data)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            pass
    finally:
        wsm.broadcast = original_broadcast.__get__(wsm, type(wsm))  # type: ignore[attr-defined]

    response_data = bytes(collected[:max_bytes])
    return {
        "status": "ok",
        "bytes_written": len(data),
        "response": response_data.decode("utf-8", errors="replace"),
        "response_b64": base64.b64encode(response_data).decode(),
        "response_bytes": len(response_data),
    }


# ─── Session Logs ───

@router.get("/api/ports/{port_id}/logs")
async def list_session_logs(port_id: str, user: dict = Depends(get_current_user)):
    sl = _get_session_logger()
    return {"port_id": port_id, "logs": sl.list_logs(port_id)}


@router.get("/api/ports/{port_id}/log")
async def get_session_log_tail(
    port_id: str,
    max_bytes: int = Query(default=65536, le=1048576),
    user: dict = Depends(get_current_user),
):
    """Get the tail of the current session log."""
    sl = _get_session_logger()
    data = sl.read_log_tail(port_id, max_bytes)
    return {
        "port_id": port_id,
        "data": data.decode("utf-8", errors="replace"),
        "data_b64": base64.b64encode(data).decode(),
        "bytes": len(data),
    }


@router.get("/api/ports/{port_id}/logs/{filename}")
async def download_session_log(port_id: str, filename: str, user: dict = Depends(get_current_user)):
    sl = _get_session_logger()
    try:
        fpath = sl.get_log_file(port_id, filename)
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=404, detail=str(e))

    def iterfile():
        with open(fpath, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(iterfile(), media_type="application/octet-stream",
                             headers={"Content-Disposition": f"attachment; filename={filename}"})


# ─── Audit Log ───

@router.get("/api/audit")
async def query_audit(
    since: Optional[str] = None,
    event: Optional[str] = None,
    user_filter: Optional[str] = Query(None, alias="user"),
    port_id: Optional[str] = None,
    limit: int = Query(default=100, le=1000),
    user: dict = Depends(require_role("admin")),
):
    al = _get_audit_logger()
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid 'since' datetime format")
    return {"events": al.query(since=since_dt, event=event, user=user_filter,
                               port_id=port_id, limit=limit)}


# ─── Terminal Recordings ───

@router.get("/api/ports/{port_id}/recordings")
async def list_recordings(port_id: str, user: dict = Depends(get_current_user)):
    from serwebs.recording import get_recorder
    recorder = get_recorder()
    if not recorder:
        return {"recordings": []}
    return {"recordings": recorder.list_recordings(port_id)}


@router.post("/api/ports/{port_id}/recordings/start")
async def start_recording(port_id: str, user: dict = Depends(require_role("user"))):
    if user.get("role") == "viewer":
        raise HTTPException(status_code=403, detail="Viewers cannot start recordings")
    from serwebs.recording import get_recorder
    recorder = get_recorder()
    if not recorder:
        raise HTTPException(status_code=400, detail="Recording is disabled")
    pm = _get_port_manager()
    if not pm.get_worker(port_id):
        raise HTTPException(status_code=400, detail="Port is not open")
    rec_id = recorder.start(port_id, user["username"])
    _get_audit_logger().log("recording_start", user=user["username"], port_id=port_id,
                            details={"recording_id": rec_id})
    return {"status": "recording", "recording_id": rec_id}


@router.post("/api/ports/{port_id}/recordings/stop")
async def stop_recording(port_id: str, user: dict = Depends(require_role("user"))):
    from serwebs.recording import get_recorder
    recorder = get_recorder()
    if not recorder:
        raise HTTPException(status_code=400, detail="Recording is disabled")
    result = recorder.stop(port_id)
    if not result:
        raise HTTPException(status_code=400, detail="No active recording for this port")
    _get_audit_logger().log("recording_stop", user=user["username"], port_id=port_id,
                            details={"recording_id": result["id"]})
    return result


@router.get("/api/ports/{port_id}/recordings/{rec_id}")
async def get_recording(port_id: str, rec_id: str, user: dict = Depends(get_current_user)):
    from serwebs.recording import get_recorder
    recorder = get_recorder()
    if not recorder:
        raise HTTPException(status_code=400, detail="Recording is disabled")
    try:
        fpath = recorder.get_recording_path(port_id, rec_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Recording not found")

    def iterfile():
        with open(fpath, "rb") as f:
            while chunk := f.read(65536):
                yield chunk

    return StreamingResponse(iterfile(), media_type="application/json",
                             headers={"Content-Disposition": f"attachment; filename={rec_id}.cast"})


@router.delete("/api/ports/{port_id}/recordings/{rec_id}")
async def delete_recording(port_id: str, rec_id: str, user: dict = Depends(require_role("admin"))):
    from serwebs.recording import get_recorder
    recorder = get_recorder()
    if not recorder:
        raise HTTPException(status_code=400, detail="Recording is disabled")
    try:
        recorder.delete_recording(port_id, rec_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Recording not found")
    _get_audit_logger().log("recording_delete", user=user["username"], port_id=port_id,
                            details={"recording_id": rec_id})
    return {"status": "deleted"}


# ─── Aggregator ───

@router.get("/api/aggregator/ports")
async def aggregator_ports(user: dict = Depends(get_current_user)):
    """Fetch ports from all configured backends."""
    from serwebs.aggregator import get_aggregator
    agg = get_aggregator()
    if not agg:
        raise HTTPException(status_code=400, detail="Aggregator is not enabled")
    ports = await agg.fetch_all_ports()
    return {"ports": ports, "backends": [b.name for b in agg.backends]}


@router.get("/api/aggregator/backends")
async def aggregator_backends(user: dict = Depends(get_current_user)):
    from serwebs.aggregator import get_aggregator
    agg = get_aggregator()
    if not agg:
        raise HTTPException(status_code=400, detail="Aggregator is not enabled")
    return {"backends": [{"name": b.name, "url": b.url} for b in agg.backends]}


@router.post("/api/aggregator/backends/reload")
async def aggregator_reload(user: dict = Depends(require_role("admin"))):
    from serwebs.aggregator import get_aggregator
    agg = get_aggregator()
    if not agg:
        raise HTTPException(status_code=400, detail="Aggregator is not enabled")
    agg.reload_backends()
    _get_audit_logger().log("aggregator_reload", user=user["username"])
    return {"backends": [b.name for b in agg.backends]}


@router.get("/api/aggregator/ws-url/{backend_name}/{port_id}")
async def aggregator_ws_url(backend_name: str, port_id: str, user: dict = Depends(get_current_user)):
    """Get WebSocket URL for connecting to a port on a remote backend."""
    from serwebs.aggregator import get_aggregator
    agg = get_aggregator()
    if not agg:
        raise HTTPException(status_code=400, detail="Aggregator is not enabled")
    url = await agg.get_backend_ws_url(backend_name, port_id)
    if not url:
        raise HTTPException(status_code=404, detail=f"Backend '{backend_name}' not found")
    return {"ws_url": url}


@router.api_route("/api/aggregator/proxy/{backend_name}/{path:path}",
                  methods=["GET", "POST", "PUT", "DELETE"])
async def aggregator_proxy(backend_name: str, path: str, request: Request,
                           user: dict = Depends(require_role("user"))):
    """Proxy a request to a specific backend."""
    from serwebs.aggregator import get_aggregator
    agg = get_aggregator()
    if not agg:
        raise HTTPException(status_code=400, detail="Aggregator is not enabled")
    body = None
    if request.method in ("POST", "PUT"):
        try:
            body = await request.json()
        except Exception:
            pass
    result = await agg.proxy_request(backend_name, f"/{path}", request.method, body)
    if "error" in result:
        raise HTTPException(status_code=502, detail=result["error"])
    return result


# ─── Health / Metrics ───

@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        version=__version__,
        uptime_seconds=round(time.monotonic() - _start_time, 1),
    )


@router.get("/metrics", response_model=MetricsResponse)
async def metrics(user: dict = Depends(require_role("admin"))):
    pm = _get_port_manager()
    ws = _get_ws_manager()
    ports_info = {}
    for port in pm.get_ports():
        if port.status == PortStatus.OPEN:
            ports_info[port.id] = {
                "device": port.device,
                "baudrate": port.settings.baudrate if port.settings else None,
                "clients": ws.client_count(port.id),
            }
    return MetricsResponse(
        uptime_seconds=round(time.monotonic() - _start_time, 1),
        open_ports=pm.open_port_count,
        total_clients=ws.total_clients(),
        ports=ports_info,
    )
