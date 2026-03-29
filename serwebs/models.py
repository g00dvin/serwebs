from __future__ import annotations

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class PortStatus(str, Enum):
    FREE = "free"
    OPEN = "open"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"


class Parity(str, Enum):
    NONE = "none"
    EVEN = "even"
    ODD = "odd"


class FlowControl(str, Enum):
    NONE = "none"
    RTSCTS = "rtscts"
    XONXOFF = "xonxoff"


class PortSettings(BaseModel):
    baudrate: int = 115200
    parity: Parity = Parity.NONE
    stopbits: int = Field(default=1, ge=1, le=2)
    databits: int = Field(default=8, ge=7, le=8)
    flowcontrol: FlowControl = FlowControl.NONE
    read_timeout: Optional[float] = None
    write_timeout: Optional[float] = None


class PortInfo(BaseModel):
    id: str
    device: str
    description: str = ""
    alias: str = ""
    status: PortStatus = PortStatus.FREE
    settings: Optional[PortSettings] = None
    clients: int = 0
    tags: List[str] = Field(default_factory=list)


class PortOpenRequest(BaseModel):
    settings: PortSettings = Field(default_factory=PortSettings)


class WsMessage(BaseModel):
    type: str
    payload: Optional[str] = None
    message: Optional[str] = None
    state: Optional[str] = None
    timestamp: Optional[str] = None


class PortRenameRequest(BaseModel):
    alias: str = ""


class ErrorResponse(BaseModel):
    error: str
    details: str = ""


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginRequest(BaseModel):
    username: str
    password: str


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = ""
    uptime_seconds: float = 0.0


class MetricsResponse(BaseModel):
    uptime_seconds: float = 0.0
    open_ports: int = 0
    total_clients: int = 0
    ports: dict = Field(default_factory=dict)
