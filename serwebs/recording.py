from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from serwebs.config import get_config

logger = logging.getLogger("serwebs.recording")

_recorder: Optional["Recorder"] = None


def init_recorder(data_dir: Path, max_storage_mb: int = 500) -> "Recorder":
    global _recorder
    _recorder = Recorder(data_dir / "recordings", max_storage_mb)
    return _recorder


def get_recorder() -> Optional["Recorder"]:
    return _recorder


class Recorder:
    """Records terminal I/O in asciicast v2 format."""

    def __init__(self, rec_dir: Path, max_storage_mb: int = 500):
        self._rec_dir = rec_dir
        self._max_bytes = max_storage_mb * 1024 * 1024
        self._rec_dir.mkdir(parents=True, exist_ok=True)
        self._active: dict[str, _ActiveRecording] = {}

    def start(self, port_id: str, user: str) -> str:
        if port_id in self._active:
            raise ValueError(f"Already recording on port {port_id}")
        rec_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
        port_dir = self._rec_dir / port_id
        port_dir.mkdir(parents=True, exist_ok=True)
        fpath = port_dir / f"{rec_id}.cast"

        rec = _ActiveRecording(
            rec_id=rec_id,
            port_id=port_id,
            user=user,
            fpath=fpath,
            start_time=time.monotonic(),
        )
        # Write asciicast v2 header
        header = {
            "version": 2,
            "width": 120,
            "height": 40,
            "timestamp": int(datetime.now(timezone.utc).timestamp()),
            "title": f"SerWebs recording: {port_id}",
            "env": {"TERM": "xterm-256color"},
        }
        rec.file = open(fpath, "w")
        rec.file.write(json.dumps(header) + "\n")
        rec.file.flush()
        self._active[port_id] = rec
        logger.info("Recording started: %s on %s by %s", rec_id, port_id, user)
        return rec_id

    def record_data(self, port_id: str, data: bytes, direction: str = "o") -> None:
        """Record a data event. direction: 'o' for output (RX), 'i' for input (TX)."""
        rec = self._active.get(port_id)
        if not rec or not rec.file:
            return
        elapsed = time.monotonic() - rec.start_time
        # asciicast v2: [time, event_type, data]
        event = [round(elapsed, 6), direction, data.decode("utf-8", errors="replace")]
        rec.file.write(json.dumps(event) + "\n")
        rec.file.flush()

    def stop(self, port_id: str) -> Optional[dict]:
        rec = self._active.pop(port_id, None)
        if not rec:
            return None
        if rec.file:
            rec.file.close()
        duration = time.monotonic() - rec.start_time
        size = rec.fpath.stat().st_size if rec.fpath.exists() else 0
        logger.info("Recording stopped: %s (%.1fs, %d bytes)", rec.rec_id, duration, size)
        return {
            "id": rec.rec_id,
            "port_id": port_id,
            "duration": round(duration, 1),
            "size": size,
        }

    def is_recording(self, port_id: str) -> bool:
        return port_id in self._active

    def list_recordings(self, port_id: str) -> List[dict]:
        port_dir = self._rec_dir / port_id
        if not port_dir.exists():
            return []
        result = []
        for f in sorted(port_dir.iterdir(), reverse=True):
            if f.suffix == ".cast" and f.is_file():
                stat = f.stat()
                # Read header for metadata
                meta = {"width": 120, "height": 40}
                try:
                    with open(f) as fp:
                        header = json.loads(fp.readline())
                        meta = header
                except Exception:
                    pass
                result.append({
                    "id": f.stem,
                    "filename": f.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "timestamp": meta.get("timestamp"),
                })
        return result

    def get_recording_path(self, port_id: str, rec_id: str) -> Path:
        fpath = self._rec_dir / port_id / f"{rec_id}.cast"
        if not fpath.exists():
            raise FileNotFoundError(f"Recording not found: {rec_id}")
        # Prevent path traversal
        if fpath.parent != self._rec_dir / port_id:
            raise ValueError("Invalid recording ID")
        return fpath

    def delete_recording(self, port_id: str, rec_id: str) -> None:
        fpath = self.get_recording_path(port_id, rec_id)
        fpath.unlink()
        logger.info("Recording deleted: %s/%s", port_id, rec_id)

    def cleanup_storage(self) -> None:
        """Remove oldest recordings if total storage exceeds limit."""
        total = sum(f.stat().st_size for f in self._rec_dir.rglob("*.cast") if f.is_file())
        if total <= self._max_bytes:
            return
        files = sorted(self._rec_dir.rglob("*.cast"), key=lambda f: f.stat().st_mtime)
        for f in files:
            if total <= self._max_bytes:
                break
            size = f.stat().st_size
            f.unlink()
            total -= size
            logger.info("Cleaned up old recording: %s (%d bytes)", f.name, size)


class _ActiveRecording:
    __slots__ = ("rec_id", "port_id", "user", "fpath", "start_time", "file")

    def __init__(self, rec_id: str, port_id: str, user: str, fpath: Path, start_time: float):
        self.rec_id = rec_id
        self.port_id = port_id
        self.user = user
        self.fpath = fpath
        self.start_time = start_time
        self.file = None
