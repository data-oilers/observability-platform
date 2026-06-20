from typing import Literal

from pydantic import BaseModel

Severity = Literal["INFO", "WARNING", "ERROR", "CRITICAL"]


class LogEvent(BaseModel):
    ts: str
    severity: Severity = "INFO"
    pod: str = ""
    message: str = ""
    path: str | None = None
    status: int | None = None
    duration_ms: float | None = None
    request_id: str | None = None
    user_id: str | None = None


class HealthSummary(BaseModel):
    status: Literal["ok", "warn", "crit"]
    reasons: list[str]
    errors: int
    gemini_429: int
