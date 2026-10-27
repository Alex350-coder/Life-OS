"""Reloj real del sistema (puerto `Clock`)."""

import time
from datetime import UTC, datetime, tzinfo


class SystemClock:
    def __init__(self, zone: tzinfo = UTC) -> None:
        self._zone = zone

    def now(self) -> datetime:
        return datetime.now(self._zone)

    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)
