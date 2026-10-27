"""Puerto de reloj: hora con zona, tiempo monotónico y espera, sustituibles en pruebas."""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Hora actual con zona horaria."""
        ...

    def monotonic(self) -> float: ...

    def sleep(self, seconds: float) -> None: ...
