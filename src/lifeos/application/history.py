"""Historial de cambios para la web (F5-T04): la auditoría de la bóveda, de lo más reciente a
lo más antiguo (ADR-0008).

Solo lee. Un `intento` que terminó (ok, rechazado o error, con la misma correlación, acción y
destino) se oculta; uno sin resultado se muestra como incompleto, porque la escritura pudo
ocurrir sin quedar auditada.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from lifeos.domain.errors import ValidationError
from lifeos.domain.text import printable
from lifeos.ports.logs import AuditReader, AuditRecord, AuditResult

DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MAX_FIELD_CHARS = 200
MONTHS_PER_YEAR = 12


@dataclass(frozen=True, slots=True)
class ChangeEntry:
    ts: datetime
    actor: str
    action: str
    target: str
    result: AuditResult
    reason: str | None
    completed: bool  # False: intento sin resultado registrado


@dataclass(frozen=True, slots=True)
class ChangeHistory:
    entries: tuple[ChangeEntry, ...]
    malformed: int  # líneas del registro que no se pudieron leer
    truncated: bool  # hay más registros que el límite


def _previous_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, MONTHS_PER_YEAR) if month == 1 else (year, month - 1)


def _key(record: AuditRecord) -> tuple[str, str, str]:
    return record.correlation_id, record.action, record.target


def _text(value: str) -> str:
    return printable(value)[:MAX_FIELD_CHARS]


def _entry(record: AuditRecord, *, completed: bool) -> ChangeEntry:
    return ChangeEntry(
        ts=record.ts,
        actor=_text(record.actor),
        action=_text(record.action),
        target=_text(record.target),
        result=record.result,
        reason=_text(record.reason) if record.reason is not None else None,
        completed=completed,
    )


def _pair(records: list[AuditRecord]) -> list[AuditRecord]:
    """Cada resultado cierra un intento anterior de la misma clave; los que sobran se muestran."""
    shown: list[AuditRecord] = []
    open_attempts: dict[tuple[str, str, str], list[AuditRecord]] = defaultdict(list)
    for record in sorted(records, key=lambda r: r.ts):
        key = _key(record)
        if record.result is AuditResult.INTENTO:
            open_attempts[key].append(record)
            continue
        if open_attempts[key]:
            open_attempts[key].pop(0)
        shown.append(record)
    return shown + [attempt for attempts in open_attempts.values() for attempt in attempts]


def recent_changes(
    reader: AuditReader, now: datetime, *, limit: int = DEFAULT_LIMIT
) -> ChangeHistory:
    """Cambios del mes actual y del anterior (UTC, como los logs)."""
    if not 1 <= limit <= MAX_LIMIT:
        raise ValidationError("límite", limit, f"debe estar entre 1 y {MAX_LIMIT}")
    if now.utcoffset() is None:
        raise ValidationError("ahora", now, "requiere zona horaria")
    current = now.astimezone(UTC)
    months = (_previous_month(current.year, current.month), (current.year, current.month))
    records: list[AuditRecord] = []
    malformed = 0
    for year, month in months:
        scan = reader.read_month(year, month)
        records += scan.records
        malformed += scan.malformed
    entries = [_entry(r, completed=r.result is not AuditResult.INTENTO) for r in _pair(records)]
    entries.sort(key=lambda entry: entry.ts, reverse=True)
    return ChangeHistory(tuple(entries[:limit]), malformed, len(entries) > limit)
