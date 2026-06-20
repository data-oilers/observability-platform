from typing import Protocol

from obs_backend.models import LogEvent


class LogSource(Protocol):
    def recent(self, env: str, min_severity: str = "DEFAULT", limit: int = 100) -> list[LogEvent]:
        """Devuelve los logs recientes (más nuevos primero) del namespace de app."""
        ...
