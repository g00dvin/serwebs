from __future__ import annotations

import time


class RingBuffer:
    """Fixed-size circular byte buffer for session replay."""

    def __init__(self, capacity: int = 65536):
        self._buf = bytearray(capacity)
        self._capacity = capacity
        self._write_pos = 0
        self._length = 0

    def append(self, data: bytes) -> None:
        data_len = len(data)
        if data_len == 0:
            return
        if data_len >= self._capacity:
            # Data larger than buffer — keep only the tail
            data = data[-self._capacity:]
            data_len = self._capacity
            self._buf[:] = data
            self._write_pos = 0
            self._length = self._capacity
            return

        end = self._write_pos + data_len
        if end <= self._capacity:
            self._buf[self._write_pos:end] = data
        else:
            first = self._capacity - self._write_pos
            self._buf[self._write_pos:] = data[:first]
            self._buf[:data_len - first] = data[first:]
        self._write_pos = end % self._capacity
        self._length = min(self._length + data_len, self._capacity)

    def read_all(self) -> bytes:
        if self._length == 0:
            return b""
        if self._length < self._capacity:
            return bytes(self._buf[:self._length])
        start = self._write_pos
        return bytes(self._buf[start:] + self._buf[:start])

    def clear(self) -> None:
        self._write_pos = 0
        self._length = 0


class RateLimiter:
    """Token-bucket rate limiter per key."""

    def __init__(self, rate: float = 100.0, burst: int = 0):
        self._rate = rate
        self._burst = burst or int(rate)
        self._tokens: dict[str, float] = {}
        self._last: dict[str, float] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        if key not in self._tokens:
            self._tokens[key] = float(self._burst)
            self._last[key] = now

        elapsed = now - self._last[key]
        self._last[key] = now
        self._tokens[key] = min(
            float(self._burst),
            self._tokens[key] + elapsed * self._rate,
        )
        if self._tokens[key] >= 1.0:
            self._tokens[key] -= 1.0
            return True
        return False

    def remove(self, key: str) -> None:
        self._tokens.pop(key, None)
        self._last.pop(key, None)
