from __future__ import annotations

import json
import logging
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from serwebs import __version__
from serwebs.audit import AuditLogger
from serwebs.config import AppConfig, get_config, get_config_dir, load_config
from serwebs.port_manager import PortManager
from serwebs.session_logger import SessionLogger
from serwebs.ws_manager import WsManager

_port_manager: Optional[PortManager] = None
_ws_manager: Optional[WsManager] = None
_audit_logger: Optional[AuditLogger] = None
_session_logger: Optional[SessionLogger] = None


def get_port_manager() -> PortManager:
    assert _port_manager is not None
    return _port_manager


def get_ws_manager() -> WsManager:
    assert _ws_manager is not None
    return _ws_manager


def get_audit_logger() -> AuditLogger:
    assert _audit_logger is not None
    return _audit_logger


def get_session_logger() -> SessionLogger:
    assert _session_logger is not None
    return _session_logger


def _setup_logging(cfg: AppConfig) -> None:
    level = getattr(logging, cfg.logging.level.upper(), logging.INFO)

    if cfg.logging.format == "json":
        class JsonFormatter(logging.Formatter):
            def format(self, record: logging.LogRecord) -> str:
                return json.dumps({
                    "time": self.formatTime(record),
                    "level": record.levelname,
                    "logger": record.name,
                    "message": record.getMessage(),
                })

        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
    else:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))

    root = logging.getLogger("serwebs")
    root.setLevel(level)
    root.addHandler(handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _port_manager, _ws_manager, _audit_logger, _session_logger
    cfg = get_config()
    logger = logging.getLogger("serwebs")
    data_dir = cfg.get_data_dir()

    # Initialize audit logger
    if cfg.audit.enabled:
        _audit_logger = AuditLogger(
            log_dir=data_dir / "audit",
            max_size_mb=cfg.audit.max_file_size_mb,
            max_files=cfg.audit.max_files,
        )
        logger.info("Audit logging enabled -> %s", data_dir / "audit")
    else:
        _audit_logger = AuditLogger(log_dir=data_dir / "audit")
        _audit_logger.log = lambda *a, **kw: None  # type: ignore[assignment]

    # Initialize session logger
    if cfg.session_logging.enabled:
        _session_logger = SessionLogger(
            log_dir=data_dir / "logs",
            max_size_mb=cfg.session_logging.max_file_size_mb,
            max_files=cfg.session_logging.max_files,
            timestamp_prefix=cfg.session_logging.timestamp_prefix,
        )
        logger.info("Session logging enabled -> %s", data_dir / "logs")
    else:
        _session_logger = SessionLogger(log_dir=data_dir / "logs")
        _session_logger.log_data = lambda *a, **kw: None  # type: ignore[assignment]

    # Initialize recording
    if cfg.recordings.enabled:
        from serwebs.recording import init_recorder
        recorder = init_recorder(data_dir, max_storage_mb=cfg.recordings.max_storage_mb)
        logger.info("Terminal recording enabled -> %s", data_dir / "recordings")
    else:
        from serwebs.recording import init_recorder
        recorder = init_recorder(data_dir)
        recorder.start = lambda *a, **kw: (_ for _ in ()).throw(ValueError("Recording disabled"))  # type: ignore[assignment]

    _ws_manager = WsManager(session_logger=_session_logger, recorder=recorder)
    _port_manager = PortManager(_ws_manager)

    # Initial port scan
    ports = _port_manager.scan_ports()
    logger.info("Initial scan found %d ports", len(ports))

    # Start udev monitor
    _port_manager.start_udev_monitor()

    # Start SSH gateway if enabled
    if cfg.ssh.enabled:
        from serwebs.ssh_gateway import start_ssh_gateway
        await start_ssh_gateway(
            host=cfg.server.host,
            port=cfg.ssh.port,
            host_key_file=cfg.ssh.host_key_file,
        )

    # Initialize aggregator if enabled
    if cfg.aggregator.enabled:
        from serwebs.aggregator import init_aggregator
        backends_path = Path(cfg.aggregator.backends_file)
        if not backends_path.is_absolute():
            backends_path = get_config_dir() / backends_path
        agg = init_aggregator(backends_path)
        if agg:
            logger.info("Aggregator enabled with %d backends", len(agg.backends))

    logger.info("SerWebs v%s started on %s:%d", __version__, cfg.server.host, cfg.server.port)

    yield

    # Shutdown
    logger.info("Shutting down...")
    if cfg.ssh.enabled:
        from serwebs.ssh_gateway import stop_ssh_gateway
        await stop_ssh_gateway()
    await _port_manager.shutdown()
    await _ws_manager.shutdown()
    logger.info("Shutdown complete")


def create_app(config_path: Optional[str] = None) -> FastAPI:
    if config_path:
        cfg = load_config(Path(config_path))
    else:
        cfg = get_config()
    _setup_logging(cfg)

    app = FastAPI(
        title="SerWebs — Web Serial Terminal Manager",
        version=__version__,
        lifespan=lifespan,
    )

    # Include routers
    from serwebs.routes_api import router as api_router
    from serwebs.routes_ws import router as ws_router

    app.include_router(api_router)
    app.include_router(ws_router)

    # OIDC callback — serve the SPA so JS can process the hash fragment
    from fastapi.responses import FileResponse

    static_dir = Path(cfg.server.static_dir)
    if not static_dir.is_absolute():
        static_dir = Path(__file__).resolve().parent.parent / static_dir

    @app.get("/oidc/callback")
    async def oidc_callback():
        return FileResponse(str(static_dir / "index.html"))

    # Serve static frontend files
    if static_dir.exists():
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")

    return app
