from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class LiveStatus(BaseModel):
    status: Literal["ok"] = "ok"


class ReadyStatus(BaseModel):
    status: Literal["ok"] = "ok"
    mongodb: Literal["ok"] = "ok"
    redis: Literal["ok"] = "ok"


class SnowflakeStatus(BaseModel):
    status: Literal["connected"] = "connected"
    checked_at: datetime
    latency_ms: int
    objects: dict[str, Literal["accessible"]]
