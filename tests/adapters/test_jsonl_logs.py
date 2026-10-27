"""ADR-0008: logs JSONL mensuales, sin secretos, con purga explícita a los 90 días."""

import json
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from lifeos.adapters.jsonl_logs import JsonlAuditLog, JsonlUsageLog, purge_logs
from lifeos.ports.logs import AuditRecord, AuditResult, CostStatus, LogWriteError, UsageRecord

KEY = "nvapi" + "-TESTKEY-0123456789abcdefXYZ"  # en dos partes: sin patrón real en el archivo
TS = datetime(2026, 10, 27, 9, 30, 15, tzinfo=UTC)

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _usage(**overrides: object) -> UsageRecord:
    values: dict[str, object] = {
        "ts": TS,
        "correlation_id": "c-1",
        "provider": "nvidia",
        "model": "proveedor/modelo",
        "profile": "ligero",
        "agent": "coach",
        "ritual": "prueba",
        "attempt": 1,
        "latency_ms": 840,
        "tokens_prompt": 21,
        "tokens_completion": None,
        "cost_usd": None,
        "cost_status": CostStatus.DESCONOCIDO,
        "outcome": "ok",
        "http_status": 200,
    }
    values.update(overrides)
    return UsageRecord(**values)  # type: ignore[arg-type]


def _lines(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_usage_record_is_one_json_line_in_monthly_file(tmp_path: Path) -> None:
    log = JsonlUsageLog(tmp_path)
    log.append(_usage())
    log.append(_usage(attempt=2, cost_usd=Decimal("0.000123"), cost_status=CostStatus.ESTIMADO))
    rows = _lines(tmp_path / "usage-2026-10.jsonl")
    assert rows[0] == {
        "ts": "2026-10-27T09:30:15Z",
        "correlation_id": "c-1",
        "provider": "nvidia",
        "model": "proveedor/modelo",
        "profile": "ligero",
        "agent": "coach",
        "ritual": "prueba",
        "attempt": 1,
        "latency_ms": 840,
        "tokens_prompt": 21,
        "tokens_completion": None,
        "cost_usd": None,
        "cost_status": "desconocido",
        "outcome": "ok",
        "http_status": 200,
    }
    assert rows[1]["cost_usd"] == "0.000123"


def test_month_is_chosen_in_utc(tmp_path: Path) -> None:
    lima = timezone(timedelta(hours=-5))
    JsonlUsageLog(tmp_path).append(_usage(ts=datetime(2026, 10, 31, 23, 30, tzinfo=lima)))
    assert (tmp_path / "usage-2026-11.jsonl").exists()


def test_naive_timestamps_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(LogWriteError, match="zona"):
        JsonlUsageLog(tmp_path).append(_usage(ts=datetime(2026, 10, 27, 9, 30)))


def test_audit_record_fields(tmp_path: Path) -> None:
    JsonlAuditLog(tmp_path).append(
        AuditRecord(
            ts=TS,
            correlation_id="c-9",
            actor="coach",
            action="anexar_checkin",
            target="Diario/2026-10-27.md",
            result=AuditResult.RECHAZADO,
            reason="permiso_denegado",
        )
    )
    assert _lines(tmp_path / "audit-2026-10.jsonl") == [
        {
            "ts": "2026-10-27T09:30:15Z",
            "correlation_id": "c-9",
            "actor": "coach",
            "action": "anexar_checkin",
            "target": "Diario/2026-10-27.md",
            "result": "rechazado",
            "reason": "permiso_denegado",
        }
    ]


# --- Redacción (SEC-08) ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides",
    [
        {"ritual": f"x{KEY}x"},  # valor exacto de una clave conocida
        {"model": "nvapi-" + "A" * 20},  # patrón de clave
        {"agent": "Bearer abc"},  # cabecera de autorización
        {"correlation_id": "sk-" + "b" * 20},
    ],
)
def test_records_containing_secrets_are_refused_and_not_written(
    tmp_path: Path, overrides: dict[str, object]
) -> None:
    log = JsonlUsageLog(tmp_path, secrets=(KEY,))
    with pytest.raises(LogWriteError, match="secreto") as info:
        log.append(_usage(**overrides))
    assert KEY not in str(info.value)
    assert not (tmp_path / "usage-2026-10.jsonl").exists()


def test_unwritable_log_dir_raises_log_write_error(tmp_path: Path) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("no soy una carpeta", encoding="utf-8")
    with pytest.raises(LogWriteError):
        JsonlUsageLog(blocker).append(_usage())


# --- Purga explícita (90 días) --------------------------------------------------------------


def _touch(directory: Path, *names: str) -> None:
    for name in names:
        (directory / name).write_text("{}\n", encoding="utf-8")


def test_purge_only_selects_closed_months_older_than_retention(tmp_path: Path) -> None:
    _touch(
        tmp_path,
        "usage-2026-06.jsonl",  # cerró el 1 de julio: 118 días → se purga
        "audit-2025-01.jsonl",
        "usage-2026-07.jsonl",  # cerró el 1 de agosto: 87 días → se conserva
        "usage-2026-10.jsonl",
        "usage-2026-6.jsonl",  # nombre no exacto: nunca se toca
        "notas.txt",
        "otro-2020-01.jsonl",
    )
    now = datetime(2026, 10, 27, tzinfo=UTC)
    selected = purge_logs(tmp_path, now)
    assert sorted(p.name for p in selected) == ["audit-2025-01.jsonl", "usage-2026-06.jsonl"]
    assert all(p.exists() for p in selected)  # por defecto solo simula

    purge_logs(tmp_path, now, dry_run=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "notas.txt",
        "otro-2020-01.jsonl",
        "usage-2026-07.jsonl",
        "usage-2026-10.jsonl",
        "usage-2026-6.jsonl",
    ]


def test_purge_requires_aware_now_and_sane_retention(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        purge_logs(tmp_path, datetime(2026, 10, 27))
    with pytest.raises(ValueError):
        purge_logs(tmp_path, datetime(2026, 10, 27, tzinfo=UTC), retention_days=0)


def test_purge_of_missing_dir_is_empty(tmp_path: Path) -> None:
    assert purge_logs(tmp_path / "no-existe", datetime(2026, 10, 27, tzinfo=UTC)) == ()
