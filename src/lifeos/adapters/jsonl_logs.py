"""Logs JSON Lines mensuales en `project/logs/` (ADR-0008).

Cada registro se serializa campo a campo desde un dataclass tipado y pasa por un filtro que lo
rechaza si contiene una clave conocida, un patrón de clave o una cabecera `Bearer` (SEC-08).
Nada se borra automáticamente: `purge_logs` es explícita y por defecto solo simula.
"""

import dataclasses
import json
import os
import re
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from pathlib import Path

from lifeos.ports.logs import AuditRecord, LogWriteError, UsageRecord

RETENTION_DAYS = 90
_LOG_NAME_RE = re.compile(r"^(usage|audit)-(\d{4})-(0[1-9]|1[0-2])\.jsonl$")
# Mismos patrones de clave que verifica tests/test_repo_hygiene.py, más la cabecera.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9]{16,}|nvapi-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|Bearer\s)"
)


def _json_value(value: object) -> object:
    if isinstance(value, datetime):
        if value.utcoffset() is None:
            raise LogWriteError("el registro necesita una hora con zona horaria")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    return value


class _JsonlLog:
    def __init__(self, directory: Path, prefix: str, secrets: tuple[str, ...]) -> None:
        self._dir = directory
        self._prefix = prefix
        self._secrets = tuple(secret for secret in secrets if secret)
        self._lock = threading.Lock()

    def _line(self, record: UsageRecord | AuditRecord) -> str:
        fields = {f.name: _json_value(getattr(record, f.name)) for f in dataclasses.fields(record)}
        line = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
        if _SECRET_RE.search(line) or any(secret in line for secret in self._secrets):
            raise LogWriteError("registro rechazado: contiene un secreto o una cabecera sensible")
        return line

    def _append(self, record: UsageRecord | AuditRecord) -> None:
        line = self._line(record)
        month = record.ts.astimezone(UTC).strftime("%Y-%m")
        path = self._dir / f"{self._prefix}-{month}.jsonl"
        try:
            with self._lock:
                self._dir.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
        except OSError as error:
            raise LogWriteError(f"no se pudo escribir {path.name}: {error.strerror}") from None


class JsonlUsageLog(_JsonlLog):
    def __init__(self, directory: Path, *, secrets: tuple[str, ...] = ()) -> None:
        super().__init__(directory, "usage", secrets)

    def append(self, record: UsageRecord) -> None:
        self._append(record)


class JsonlAuditLog(_JsonlLog):
    def __init__(self, directory: Path, *, secrets: tuple[str, ...] = ()) -> None:
        super().__init__(directory, "audit", secrets)

    def append(self, record: AuditRecord) -> None:
        self._append(record)


def _month_end(year: int, month: int) -> datetime:
    return datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)


def purge_logs(
    directory: Path,
    now: datetime,
    *,
    retention_days: int = RETENTION_DAYS,
    dry_run: bool = True,
) -> tuple[Path, ...]:
    """Archivos de meses cerrados hace más de `retention_days`; los borra solo si `dry_run=False`.

    Solo considera nombres exactos `usage-AAAA-MM.jsonl` y `audit-AAAA-MM.jsonl` y nunca sigue
    enlaces: cualquier otro archivo de la carpeta queda intacto.
    """
    if now.utcoffset() is None:
        raise ValueError("now necesita zona horaria")
    if retention_days < 1:
        raise ValueError("la retención debe ser de al menos un día")
    if not directory.is_dir():
        return ()
    limit = timedelta(days=retention_days)
    selected = tuple(
        path
        for path in sorted(directory.iterdir())
        if (match := _LOG_NAME_RE.match(path.name))
        and path.is_file()
        and not path.is_symlink()
        and now - _month_end(int(match[2]), int(match[3])) > limit
    )
    if not dry_run:
        for path in selected:
            try:
                path.unlink()
            except OSError as error:
                raise LogWriteError(f"no se pudo purgar {path.name}: {error.strerror}") from None
    return selected
