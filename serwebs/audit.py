from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("serwebs.audit")


class AuditLogger:
    """Writes audit events as JSON lines with file rotation."""

    def __init__(self, log_dir: Path, max_size_mb: int = 10, max_files: int = 5):
        self._log_dir = log_dir
        self._max_bytes = max_size_mb * 1024 * 1024
        self._max_files = max_files
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._log_file = self._log_dir / "audit.jsonl"

    def log(self, event: str, user: str = "", port_id: str = "", details: Optional[dict] = None) -> None:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "user": user,
            "port": port_id,
        }
        if details:
            entry["details"] = details
        try:
            self._rotate_if_needed()
            with open(self._log_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.error("Audit write failed: %s", e)

        # Forward to alerting
        try:
            from serwebs.alerting import get_alerter
            alerter = get_alerter()
            if alerter:
                alerter.send(event, user=user, port_id=port_id, **(details or {}))
        except Exception:
            pass

        # Forward to syslog
        try:
            from serwebs.syslog_handler import get_syslog
            syslog_fwd = get_syslog()
            if syslog_fwd:
                severity = 4 if "fail" in event else 6  # warning for failures, info for rest
                syslog_fwd.send(event, severity=severity, user=user, port_id=port_id)
        except Exception:
            pass

    def query(
        self,
        since: Optional[datetime] = None,
        event: Optional[str] = None,
        user: Optional[str] = None,
        port_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        results: list[dict] = []
        files = self._get_log_files()
        for fpath in files:
            try:
                with open(fpath, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if since and entry.get("ts", "") < since.isoformat():
                            continue
                        if event and entry.get("event") != event:
                            continue
                        if user and entry.get("user") != user:
                            continue
                        if port_id and entry.get("port") != port_id:
                            continue
                        results.append(entry)
            except OSError:
                continue
        # Return newest first, limited
        results.reverse()
        return results[:limit]

    def _rotate_if_needed(self) -> None:
        if not self._log_file.exists():
            return
        try:
            size = self._log_file.stat().st_size
        except OSError:
            return
        if size < self._max_bytes:
            return
        # Rotate: audit.jsonl.4 -> delete, .3 -> .4, ... .1 -> .2, current -> .1
        for i in range(self._max_files, 0, -1):
            src = self._log_dir / f"audit.jsonl.{i}"
            if i == self._max_files:
                src.unlink(missing_ok=True)
            else:
                dst = self._log_dir / f"audit.jsonl.{i + 1}"
                if src.exists():
                    src.rename(dst)
        if self._log_file.exists():
            self._log_file.rename(self._log_dir / "audit.jsonl.1")

    def _get_log_files(self) -> list[Path]:
        """Return log files ordered oldest-first."""
        files = []
        for i in range(self._max_files, 0, -1):
            f = self._log_dir / f"audit.jsonl.{i}"
            if f.exists():
                files.append(f)
        if self._log_file.exists():
            files.append(self._log_file)
        return files
