"""Lectura del log de uso (F4-T05, ADR-0008): tolerante con líneas dañadas y sin reproducirlas."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from lifeos.adapters.jsonl_logs import JsonlUsageLog, JsonlUsageReader
from lifeos.ports.logs import CostStatus, UsageOutcome, UsageRecord

pytestmark = [pytest.mark.unit]

TS = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


def record(**changes: object) -> UsageRecord:
    base: dict[str, object] = {
        "ts": TS,
        "correlation_id": "c1",
        "provider": "nvidia",
        "model": "proveedor/modelo-ligero",
        "profile": "ligero",
        "agent": "coach",
        "ritual": "checkin",
        "attempt": 1,
        "latency_ms": 120,
        "tokens_prompt": 10,
        "tokens_completion": 5,
        "cost_usd": None,
        "cost_status": CostStatus.DESCONOCIDO,
        "outcome": UsageOutcome.OK,
        "http_status": 200,
    }
    base.update(changes)
    return UsageRecord(**base)  # type: ignore[arg-type]


def test_reads_back_what_the_log_wrote(tmp_path: Path) -> None:
    log = JsonlUsageLog(tmp_path)
    first = record()
    second = record(
        correlation_id="c2",
        cost_usd=Decimal("0.0012"),
        cost_status=CostStatus.ESTIMADO,
        outcome=UsageOutcome.TIMEOUT,
        tokens_prompt=None,
        tokens_completion=None,
        http_status=None,
    )
    log.append(first)
    log.append(second)
    scan = JsonlUsageReader(tmp_path).read_month(2026, 11)
    assert scan.records == (first, second)
    assert scan.malformed == 0
    assert scan.files == 1


def test_missing_month_or_directory_is_empty(tmp_path: Path) -> None:
    assert JsonlUsageReader(tmp_path / "no-existe").read_month(2026, 11).records == ()
    scan = JsonlUsageReader(tmp_path).read_month(2026, 10)
    assert (scan.records, scan.malformed, scan.files) == ((), 0, 0)


def test_damaged_lines_are_counted_and_skipped(tmp_path: Path) -> None:
    JsonlUsageLog(tmp_path).append(record())
    path = tmp_path / "usage-2026-11.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write("{no es json\n")
        handle.write('{"ts": "2026-11-02T14:00:00Z"}\n')  # faltan campos
        handle.write("[1, 2]\n")
        handle.write("\n")
        good = path.read_text(encoding="utf-8").splitlines()[0]
        handle.write(good.replace('"ok"', '"inventado"') + "\n")  # resultado desconocido
        handle.write(good.replace('"attempt":1', '"attempt":0') + "\n")  # invariante rota
        handle.write(good.replace('"coach"', '"coach","extra":1') + "\n")  # clave desconocida
        handle.write(good[: len(good) // 2])  # última línea cortada
    scan = JsonlUsageReader(tmp_path).read_month(2026, 11)
    assert len(scan.records) == 1
    assert scan.malformed == 7


@pytest.mark.parametrize("month", [0, 13])
def test_invalid_month_is_rejected(tmp_path: Path, month: int) -> None:
    with pytest.raises(ValueError):
        JsonlUsageReader(tmp_path).read_month(2026, month)


def test_naive_or_non_utc_timestamps_are_malformed(tmp_path: Path) -> None:
    JsonlUsageLog(tmp_path).append(record())
    path = tmp_path / "usage-2026-11.jsonl"
    good = path.read_text(encoding="utf-8").splitlines()[0]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(good.replace("Z", "") + "\n")
    assert JsonlUsageReader(tmp_path).read_month(2026, 11).malformed == 1
