from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

from pydantic import BaseModel, Field

if sys.version_info >= (3, 11):
    import tomllib
else:
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.toml"


class UserConfig(BaseModel):
    username: str
    password_hash: str
    role: str = "user"  # admin, user, viewer


class OIDCConfig(BaseModel):
    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    jwks_uri: str = ""
    username_claim: str = "preferred_username"
    role_claim: str = "groups"
    admin_groups: List[str] = Field(default_factory=lambda: ["serwebs-admin"])
    viewer_groups: List[str] = Field(default_factory=lambda: ["serwebs-viewer"])
    default_role: str = "user"


class AuthConfig(BaseModel):
    secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    algorithm: str = "HS256"
    token_expire_minutes: int = 480
    users: List[UserConfig] = Field(default_factory=list)
    oidc: OIDCConfig = Field(default_factory=OIDCConfig)


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    static_dir: str = "frontend"


class SerialConfig(BaseModel):
    port_patterns: List[str] = Field(default_factory=lambda: ["/dev/ttyUSB*", "/dev/ttyACM*"])
    blacklist_patterns: List[str] = Field(default_factory=lambda: ["/dev/ttyS*"])
    ring_buffer_size: int = 65536
    max_message_size: int = 4096
    rate_limit_per_second: int = 100
    max_clients_per_port: int = 10
    max_ports: int = 20
    read_timeout: float = 0.1
    write_timeout: float = 1.0


class LoggingConfig(BaseModel):
    level: str = "info"
    format: str = "json"


class DataConfig(BaseModel):
    directory: str = "data"


class AuditConfig(BaseModel):
    enabled: bool = True
    max_file_size_mb: int = 10
    max_files: int = 5


class SessionLoggingConfig(BaseModel):
    enabled: bool = True
    max_file_size_mb: int = 50
    max_files: int = 5
    timestamp_prefix: bool = True


class RecordingsConfig(BaseModel):
    enabled: bool = True
    max_storage_mb: int = 500


class SSHConfig(BaseModel):
    enabled: bool = False
    port: int = 2222
    host_key_file: str = ""


class AggregatorConfig(BaseModel):
    enabled: bool = False
    backends_file: str = "backends.yaml"


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    serial: SerialConfig = Field(default_factory=SerialConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    session_logging: SessionLoggingConfig = Field(default_factory=SessionLoggingConfig)
    recordings: RecordingsConfig = Field(default_factory=RecordingsConfig)
    ssh: SSHConfig = Field(default_factory=SSHConfig)
    aggregator: AggregatorConfig = Field(default_factory=AggregatorConfig)

    def get_data_dir(self) -> Path:
        """Resolve data directory (relative to config file or absolute)."""
        d = Path(self.data.directory)
        if not d.is_absolute():
            config_path = Path(os.environ.get("SERWEBS_CONFIG", str(_DEFAULT_CONFIG_PATH)))
            d = config_path.parent / d
        d.mkdir(parents=True, exist_ok=True)
        return d


_config: Optional[AppConfig] = None
_config_path: Optional[Path] = None


def load_config(path: Optional[Path] = None) -> AppConfig:
    global _config, _config_path
    _config_path = Path(os.environ.get("SERWEBS_CONFIG", str(path or _DEFAULT_CONFIG_PATH)))
    if _config_path.exists():
        with open(_config_path, "rb") as f:
            data = tomllib.load(f)
    else:
        data = {}
    _config = AppConfig(**data)
    return _config


def get_config() -> AppConfig:
    if _config is None:
        return load_config()
    return _config


def get_config_dir() -> Path:
    """Return directory containing config.toml."""
    p = _config_path or Path(os.environ.get("SERWEBS_CONFIG", str(_DEFAULT_CONFIG_PATH)))
    return p.parent


# --- Persistent JSON storage helpers ---

def _json_path(filename: str) -> Path:
    """Resolve path for persistent JSON data in the data directory."""
    cfg = get_config()
    data_dir = cfg.get_data_dir()
    return data_dir / filename


def _load_json(filename: str) -> dict:
    p = _json_path(filename)
    if p.exists():
        try:
            with open(p, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_json(filename: str, data: dict) -> None:
    p = _json_path(filename)
    with open(p, "w") as f:
        json.dump(data, f, indent=2)


# --- Port aliases ---

def load_port_aliases() -> Dict[str, str]:
    return _load_json("port_aliases.json")


def save_port_aliases(aliases: Dict[str, str]) -> None:
    _save_json("port_aliases.json", aliases)


def set_port_alias(port_id: str, alias: str) -> Dict[str, str]:
    aliases = load_port_aliases()
    if alias.strip():
        aliases[port_id] = alias.strip()
    else:
        aliases.pop(port_id, None)
    save_port_aliases(aliases)
    return aliases


def get_port_alias(port_id: str) -> Optional[str]:
    return load_port_aliases().get(port_id)


# --- Port tags ---

def load_port_tags() -> Dict[str, List[str]]:
    return _load_json("port_tags.json")


def save_port_tags(tags: Dict[str, List[str]]) -> None:
    _save_json("port_tags.json", tags)


def set_port_tags(port_id: str, tags: List[str]) -> Dict[str, List[str]]:
    all_tags = load_port_tags()
    cleaned = [t.strip().lower() for t in tags if t.strip()]
    if cleaned:
        all_tags[port_id] = cleaned
    else:
        all_tags.pop(port_id, None)
    save_port_tags(all_tags)
    return all_tags


def get_all_tag_names() -> List[str]:
    all_tags = load_port_tags()
    seen: set[str] = set()
    for tags in all_tags.values():
        seen.update(tags)
    return sorted(seen)


# --- Port profiles ---

def load_port_profiles() -> dict:
    return _load_json("port_profiles.json")


def save_port_profiles(profiles: dict) -> None:
    _save_json("port_profiles.json", profiles)


def set_port_profile(port_id: str, profile: dict) -> dict:
    profiles = load_port_profiles()
    profiles[port_id] = profile
    save_port_profiles(profiles)
    return profiles


def delete_port_profile(port_id: str) -> dict:
    profiles = load_port_profiles()
    profiles.pop(port_id, None)
    save_port_profiles(profiles)
    return profiles
