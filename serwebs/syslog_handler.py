"""Syslog forwarding — sends audit events to a remote syslog server.

Supports UDP and TCP transport, RFC 3164 and RFC 5424 formats.
Integrates with the AuditLogger to forward all audit events.
"""
from __future__ import annotations

import json
import logging
import logging.handlers
import socket
from datetime import datetime, timezone
from typing import Optional

from serwebs.config import get_config

logger = logging.getLogger("serwebs.syslog")

_syslog_handler: Optional["SyslogForwarder"] = None

# Facility map
_FACILITY_MAP = {
    "kern": 0, "user": 1, "mail": 2, "daemon": 3,
    "auth": 4, "syslog": 5, "lpr": 6, "news": 7,
    "local0": 16, "local1": 17, "local2": 18, "local3": 19,
    "local4": 20, "local5": 21, "local6": 22, "local7": 23,
}


def init_syslog() -> Optional["SyslogForwarder"]:
    global _syslog_handler
    cfg = get_config().syslog
    if not cfg.enabled:
        return None
    _syslog_handler = SyslogForwarder(cfg)
    logger.info("Syslog forwarding enabled -> %s:%d (%s)", cfg.host, cfg.port, cfg.protocol)
    return _syslog_handler


def get_syslog() -> Optional["SyslogForwarder"]:
    return _syslog_handler


class SyslogForwarder:
    def __init__(self, cfg):
        self._cfg = cfg
        self._facility = _FACILITY_MAP.get(cfg.facility, 16)  # default local0
        self._sock: Optional[socket.socket] = None
        self._connect()

    def _connect(self) -> None:
        try:
            if self._cfg.protocol == "tcp":
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._sock.settimeout(5)
                self._sock.connect((self._cfg.host, self._cfg.port))
            else:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        except Exception as e:
            logger.warning("Syslog connection failed: %s", e)
            self._sock = None

    def send(self, event: str, severity: int = 6, **kwargs) -> None:
        """Send an audit event to syslog. severity: 6=info, 4=warning, 3=error."""
        if not self._sock:
            self._connect()
            if not self._sock:
                return

        try:
            if self._cfg.format == "rfc5424":
                msg = self._format_rfc5424(event, severity, kwargs)
            else:
                msg = self._format_rfc3164(event, severity, kwargs)

            data = msg.encode("utf-8")
            if self._cfg.protocol == "tcp":
                self._sock.sendall(data + b"\n")
            else:
                self._sock.sendto(data, (self._cfg.host, self._cfg.port))
        except Exception as e:
            logger.debug("Syslog send failed: %s", e)
            self._sock = None

    def _format_rfc3164(self, event: str, severity: int, kwargs: dict) -> str:
        pri = self._facility * 8 + severity
        ts = datetime.now().strftime("%b %d %H:%M:%S")
        hostname = socket.gethostname()
        details = " ".join(f"{k}={v}" for k, v in kwargs.items() if v)
        return f"<{pri}>{ts} {hostname} serwebs: {event} {details}"

    def _format_rfc5424(self, event: str, severity: int, kwargs: dict) -> str:
        pri = self._facility * 8 + severity
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        hostname = socket.gethostname()
        structured = ""
        if kwargs:
            params = " ".join(f'{k}="{v}"' for k, v in kwargs.items() if v)
            structured = f" [serwebs@0 {params}]"
        return f"<{pri}>1 {ts} {hostname} serwebs - {event}{structured} {event}"

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
