"""Multi-backend aggregator — proxy mode that aggregates ports from multiple SerWebs instances.

Backends are configured via YAML file (backends.yaml by default).
Each backend's ports are prefixed with the backend name.

Example backends.yaml:
  backends:
    - name: lab-rack-1
      url: http://192.168.1.10:8080
      token: "eyJ..."
    - name: lab-rack-2
      url: http://192.168.1.11:8080
      username: admin
      password: secret
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("serwebs.aggregator")

_aggregator: Optional["Aggregator"] = None


def init_aggregator(backends_file: Path) -> Optional["Aggregator"]:
    global _aggregator
    if not backends_file.exists():
        logger.warning("Backends file not found: %s — aggregator disabled", backends_file)
        return None
    _aggregator = Aggregator(backends_file)
    return _aggregator


def get_aggregator() -> Optional["Aggregator"]:
    return _aggregator


class BackendConfig:
    __slots__ = ("name", "url", "token", "username", "password", "verify_ssl")

    def __init__(self, data: dict):
        self.name = data["name"]
        self.url = data["url"].rstrip("/")
        self.token = data.get("token", "")
        self.username = data.get("username", "")
        self.password = data.get("password", "")
        self.verify_ssl = data.get("verify_ssl", True)


class Aggregator:
    """Aggregates ports from multiple SerWebs backend instances."""

    def __init__(self, backends_file: Path):
        self._backends_file = backends_file
        self._backends: List[BackendConfig] = []
        self._cache: Dict[str, dict] = {}
        self._cache_time: float = 0
        self._cache_ttl: float = 5.0  # seconds
        self._token_cache: Dict[str, tuple] = {}  # name -> (token, timestamp)
        self.reload_backends()

    def reload_backends(self) -> None:
        try:
            import yaml
        except ImportError:
            logger.error("pyyaml not installed — aggregator cannot load backends. Install with: pip install pyyaml")
            return
        try:
            with open(self._backends_file) as f:
                data = yaml.safe_load(f) or {}
            backends_data = data.get("backends", [])
            self._backends = [BackendConfig(b) for b in backends_data]
            logger.info("Loaded %d backends from %s", len(self._backends), self._backends_file)
        except Exception as e:
            logger.error("Failed to load backends: %s", e)

    @property
    def backends(self) -> List[BackendConfig]:
        return self._backends

    async def fetch_all_ports(self) -> List[dict]:
        """Fetch ports from all backends, prefix with backend name."""
        import time
        now = time.monotonic()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            return list(self._cache.values())

        tasks = [self._fetch_backend_ports(b) for b in self._backends]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_ports = []
        self._cache.clear()
        for backend, result in zip(self._backends, results):
            if isinstance(result, Exception):
                logger.warning("Failed to fetch from %s: %s", backend.name, result)
                continue
            for port in result:
                # Prefix port ID with backend name
                port["backend"] = backend.name
                port["backend_url"] = backend.url
                port["original_id"] = port.get("id", "")
                port["id"] = f"{backend.name}/{port['original_id']}"
                if port.get("alias"):
                    port["alias"] = f"[{backend.name}] {port['alias']}"
                else:
                    port["alias"] = f"[{backend.name}] {port['original_id']}"
                all_ports.append(port)
                self._cache[port["id"]] = port

        self._cache_time = now
        return all_ports

    async def _fetch_backend_ports(self, backend: BackendConfig) -> List[dict]:
        """Fetch ports from a single backend."""
        try:
            import httpx
        except ImportError:
            logger.error("httpx not installed — aggregator cannot fetch backends. Install with: pip install httpx")
            return []

        headers = {}
        auth = None
        if backend.token:
            headers["Authorization"] = f"Bearer {backend.token}"
        elif backend.username and backend.password:
            import base64
            creds = base64.b64encode(f"{backend.username}:{backend.password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

        async with httpx.AsyncClient(verify=backend.verify_ssl, timeout=10.0) as client:
            resp = await client.get(f"{backend.url}/api/ports", headers=headers)
            resp.raise_for_status()
            return resp.json()

    async def proxy_request(self, backend_name: str, path: str, method: str = "GET",
                            body: Optional[dict] = None, token: str = "") -> dict:
        """Proxy an API request to a specific backend."""
        backend = self._find_backend(backend_name)
        if not backend:
            return {"error": f"Backend '{backend_name}' not found"}

        try:
            import httpx
        except ImportError:
            return {"error": "httpx not installed"}

        headers = {}
        if backend.token:
            headers["Authorization"] = f"Bearer {backend.token}"
        elif backend.username and backend.password:
            import base64
            creds = base64.b64encode(f"{backend.username}:{backend.password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

        async with httpx.AsyncClient(verify=backend.verify_ssl, timeout=10.0) as client:
            url = f"{backend.url}{path}"
            if method == "GET":
                resp = await client.get(url, headers=headers)
            elif method == "POST":
                resp = await client.post(url, headers=headers, json=body)
            elif method == "PUT":
                resp = await client.put(url, headers=headers, json=body)
            elif method == "DELETE":
                resp = await client.delete(url, headers=headers)
            else:
                return {"error": f"Unsupported method: {method}"}
            return resp.json()

    async def get_backend_ws_url(self, backend_name: str, port_id: str) -> Optional[str]:
        """Get the WebSocket URL for a port on a specific backend."""
        backend = self._find_backend(backend_name)
        if not backend:
            return None
        ws_scheme = "wss" if backend.url.startswith("https") else "ws"
        base = backend.url.replace("https://", "").replace("http://", "")
        ws_token = await self._get_backend_token(backend)
        return f"{ws_scheme}://{base}/ws/{port_id}?token={ws_token}"

    async def _get_backend_token(self, backend: BackendConfig) -> str:
        """Get a JWT token for the backend. Uses pre-configured token or logs in."""
        if backend.token:
            return backend.token

        # Check cache
        cache_key = backend.name
        cached = self._token_cache.get(cache_key)
        if cached:
            import time as _time
            if _time.monotonic() - cached[1] < 3600:  # 1 hour TTL
                return cached[0]

        # Login to get a token
        if not backend.username or not backend.password:
            return ""

        try:
            import httpx
            async with httpx.AsyncClient(verify=backend.verify_ssl, timeout=10.0) as client:
                resp = await client.post(
                    f"{backend.url}/auth/login",
                    json={"username": backend.username, "password": backend.password},
                )
                if resp.status_code == 200:
                    import time as _time
                    token = resp.json().get("access_token", "")
                    self._token_cache[cache_key] = (token, _time.monotonic())
                    return token
        except Exception as e:
            logger.warning("Failed to get token for backend %s: %s", backend.name, e)
        return ""

    def _find_backend(self, name: str) -> Optional[BackendConfig]:
        for b in self._backends:
            if b.name == name:
                return b
        return None
