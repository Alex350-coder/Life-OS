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

from lifeos.ports.logs import (
    AuditRecord,
    CostStatus,
    LogWriteError,
    UsageOutcome,
    UsageRecord,
    UsageScan,
)

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


def _ends_without_newline(path: Path) -> bool:
    """Una escritura anterior cortada (disco lleno) no debe pegar dos registros en una línea."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                return False
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"
    except FileNotFoundError:
        return False


class _JsonlLog:
    def __init__(self, directory: Path, prefix: str, secrets: tuple[str, ...]) -> None:
        self._dir = directory
        self._prefix = prefix
        # Cada secreto se busca tal cual y en su forma escapada dentro de JSON (`"` y `\\`).
        self._secrets = tuple(
            {form for secret in secrets if secret for form in (secret, json.dumps(secret)[1:-1])}
        )
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
                prefix = "\n" if _ends_without_newline(path) else ""
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(prefix + line + "\n")
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


_USAGE_FIELDS = frozenset(f.name for f in dataclasses.fields(UsageRecord))
_INT_FIELDS = ("attempt", "latency_ms")
_OPTIONAL_INT_FIELDS = ("tokens_prompt", "tokens_completion", "http_status")
_TEXT_FIELDS = ("correlation_id", "provider", "model", "profile", "agent", "ritual")
_UTC_SUFFIX = "Z"
MONTHS_PER_YEAR = 12
MAX_COST_USD = Decimal(1_000_000)  # más que esto en un intento es un registro dañado


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _usage_record(data: object) -> UsageRecord:
    """Registro tipado desde una línea JSON; cualquier desviación es `ValueError`."""
    if not isinstance(data, dict) or set(data) != _USAGE_FIELDS:
        raise ValueError("campos inesperados")
    if not all(isinstance(data[name], str) for name in (*_TEXT_FIELDS, "ts")):
        raise ValueError("texto esperado")
    if not all(_is_int(data[name]) for name in _INT_FIELDS):
        raise ValueError("entero esperado")
    if not all(data[name] is None or _is_int(data[name]) for name in _OPTIONAL_INT_FIELDS):
        raise ValueError("entero o null esperado")
    stamp = data["ts"]
    if not stamp.endswith(_UTC_SUFFIX):
        raise ValueError("la hora debe estar en UTC")
    cost = data["cost_usd"]
    if cost is not None and not isinstance(cost, str):
        raise ValueError("costo como texto decimal")
    try:
        amount = Decimal(cost) if cost is not None else None
    except ArithmeticError:
        raise ValueError("costo no decimal") from None
    if amount is not None and (not amount.is_finite() or amount < 0 or amount > MAX_COST_USD):
        raise ValueError("costo fuera de rango")
    return UsageRecord(
        ts=datetime.fromisoformat(stamp.removesuffix(_UTC_SUFFIX)).replace(tzinfo=UTC),
        correlation_id=data["correlation_id"],
        provider=data["provider"],
        model=data["model"],
        profile=data["profile"],
        agent=data["agent"],
        ritual=data["ritual"],
        attempt=data["attempt"],
        latency_ms=data["latency_ms"],
        tokens_prompt=data["tokens_prompt"],
        tokens_completion=data["tokens_completion"],
        cost_usd=amount,
        cost_status=CostStatus(data["cost_status"]),
        outcome=UsageOutcome(data["outcome"]),
        http_status=data["http_status"],
    )


class JsonlUsageReader:
    """Lee `usage-AAAA-MM.jsonl`. Las líneas dañadas se cuentan y se saltan; nunca se muestran."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory

    def read_month(self, year: int, month: int) -> UsageScan:
        if not 1 <= month <= MONTHS_PER_YEAR:
            raise ValueError("el mes debe estar entre 1 y 12")
        path = self._dir / f"usage-{year:04d}-{month:02d}.jsonl"
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return UsageScan((), 0, 0)
        except OSError as error:
            raise LogWriteError(f"no se pudo leer {path.name}: {error.strerror}") from None
        records: list[UsageRecord] = []
        malformed = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                records.append(_usage_record(json.loads(line)))
            except (ValueError, TypeError, RecursionError, ArithmeticError):
                malformed += 1
        return UsageScan(tuple(records), malformed, 1)
