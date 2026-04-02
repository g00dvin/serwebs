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


class LDAPConfig(BaseModel):
    enabled: bool = False
    url: str = "ldap://localhost:389"
    bind_dn: str = ""
    bind_password: str = ""
    user_base_dn: str = ""
    user_filter: str = "(uid={username})"
    username_attribute: str = "uid"
    group_base_dn: str = ""
    group_filter: str = "(member={user_dn})"
    admin_groups: List[str] = Field(default_factory=lambda: ["serwebs-admin"])
    viewer_groups: List[str] = Field(default_factory=lambda: ["serwebs-viewer"])
    default_role: str = "user"
    use_ssl: bool = False
    start_tls: bool = False
    ca_cert_file: str = ""


class RADIUSConfig(BaseModel):
    enabled: bool = False
    server: str = "localhost"
    port: int = 1812
    secret: str = ""
    timeout: int = 5
    retries: int = 3
    nas_identifier: str = "serwebs"
    admin_filter_id: str = "serwebs-admin"
    viewer_filter_id: str = "serwebs-viewer"
    default_role: str = "user"


class TACACSConfig(BaseModel):
    enabled: bool = False
    server: str = "localhost"
    port: int = 49
    secret: str = ""
    timeout: int = 5
    service: str = "serwebs"
    admin_priv_lvl: int = 15
    viewer_priv_lvl: int = 1
    default_role: str = "user"


class AuthConfig(BaseModel):
    secret_key: str = "CHANGE-ME-IN-PRODUCTION"
    algorithm: str = "HS256"
    token_expire_minutes: int = 480
    users: List[UserConfig] = Field(default_factory=list)
    oidc: OIDCConfig = Field(default_factory=OIDCConfig)
    ldap: LDAPConfig = Field(default_factory=LDAPConfig)
    radius: RADIUSConfig = Field(default_factory=RADIUSConfig)
    tacacs: TACACSConfig = Field(default_factory=TACACSConfig)


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


class AlertingConfig(BaseModel):
    enabled: bool = False
    # Webhook
    webhook_url: str = ""
    webhook_headers: Dict[str, str] = Field(default_factory=dict)
    # Email / SMTP
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_use_tls: bool = True
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    smtp_to: List[str] = Field(default_factory=list)
    # Events to alert on
    events: List[str] = Field(default_factory=lambda: [
        "port_open", "port_close", "device_lost", "login_failed",
        "ws_connect", "recording_start",
    ])


class SyslogConfig(BaseModel):
    enabled: bool = False
    host: str = "localhost"
    port: int = 514
    protocol: str = "udp"  # udp or tcp
    facility: str = "local0"
    format: str = "rfc5424"  # rfc3164 or rfc5424


class TelnetConfig(BaseModel):
    enabled: bool = False
    port: int = 2323
    timeout: int = 120


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
    telnet: TelnetConfig = Field(default_factory=TelnetConfig)
    alerting: AlertingConfig = Field(default_factory=AlertingConfig)
    syslog: SyslogConfig = Field(default_factory=SyslogConfig)
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


# --- Runtime users (managed via API, stored in data/users.json) ---

def load_runtime_users() -> List[Dict[str, str]]:
    """Load runtime-managed users from JSON storage."""
    data = _load_json("users.json")
    return data.get("users", [])


def save_runtime_users(users: List[Dict[str, str]]) -> None:
    _save_json("users.json", {"users": users})


# --- Port locks ---

def load_port_locks() -> Dict[str, dict]:
    return _load_json("port_locks.json")


def save_port_locks(locks: Dict[str, dict]) -> None:
    _save_json("port_locks.json", locks)
