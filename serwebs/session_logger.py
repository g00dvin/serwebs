from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List

logger = logging.getLogger("serwebs.session_log")


class SessionLogger:
    """Logs all serial I/O to per-port files with rotation."""

    def __init__(self, log_dir: Path, max_size_mb: int = 50, max_files: int = 5,
                 timestamp_prefix: bool = True):
        self._log_dir = log_dir
        self._max_bytes = max_size_mb * 1024 * 1024
        self._max_files = max_files
        self._timestamp = timestamp_prefix
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def log_data(self, port_id: str, direction: str, data: bytes) -> None:
        port_dir = self._log_dir / port_id
        port_dir.mkdir(parents=True, exist_ok=True)
        log_file = port_dir / f"{port_id}.log"

        try:
            self._rotate_if_needed(port_id, log_file)
            with open(log_file, "ab") as f:
                if self._timestamp:
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    tag = "RX" if direction == "rx" else "TX"
                    f.write(f"[{ts} {tag}] ".encode())
                f.write(data)
                if self._timestamp and not data.endswith(b"\n"):
                    f.write(b"\n")
        except OSError as e:
            logger.error("Session log write failed for %s: %s", port_id, e)

    def get_log_path(self, port_id: str) -> Path:
        return self._log_dir / port_id / f"{port_id}.log"

    def list_logs(self, port_id: str) -> List[dict]:
        port_dir = self._log_dir / port_id
        if not port_dir.exists():
            return []
        result = []
        for f in sorted(port_dir.iterdir()):
            if f.is_file() and f.name.startswith(port_id):
                stat = f.stat()
                result.append({
                    "filename": f.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
        return result

    def read_log_tail(self, port_id: str, max_bytes: int = 65536) -> bytes:
        log_file = self.get_log_path(port_id)
        if not log_file.exists():
            return b""
        size = log_file.stat().st_size
        with open(log_file, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
            return f.read()

    def get_log_file(self, port_id: str, filename: str) -> Path:
        p = self._log_dir / port_id / filename
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"Log file not found: {filename}")
        # Prevent path traversal
        if p.parent != self._log_dir / port_id:
            raise ValueError("Invalid filename")
        return p

    def _rotate_if_needed(self, port_id: str, log_file: Path) -> None:
        if not log_file.exists():
            return
        try:
            size = log_file.stat().st_size
        except OSError:
            return
        if size < self._max_bytes:
            return
        port_dir = log_file.parent
        for i in range(self._max_files, 0, -1):
            src = port_dir / f"{port_id}.log.{i}"
            if i == self._max_files:
                src.unlink(missing_ok=True)
            else:
                dst = port_dir / f"{port_id}.log.{i + 1}"
                if src.exists():
                    src.rename(dst)
        log_file.rename(port_dir / f"{port_id}.log.1")
