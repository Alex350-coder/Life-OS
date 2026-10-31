"""Meses `AAAA-MM` de los logs (ADR-0008, siempre en UTC): misma regla para la CLI y la web."""

import re
from datetime import UTC, datetime

from lifeos.domain.errors import ValidationError

_MONTH_RE = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


def parse_month(text: str) -> tuple[int, int]:
    match = _MONTH_RE.fullmatch(text.strip())
    if match is None:
        raise ValidationError("mes", text, "formato esperado AAAA-MM (mes 01 a 12)")
    return int(match[1]), int(match[2])


def current_month(now: datetime) -> tuple[int, int]:
    """Mes UTC de `now`: los logs se agrupan por mes UTC."""
    if now.utcoffset() is None:
        raise ValidationError("ahora", now, "requiere zona horaria")
    utc = now.astimezone(UTC)
    return utc.year, utc.month
