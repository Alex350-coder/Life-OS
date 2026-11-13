"""`GET /api/indicadores` (F9-T03) sobre la app real y una bóveda inventada."""

from datetime import date
from decimal import Decimal

import pytest

from lifeos.application.indicator_report import DataQuality, IndicatorReport
from lifeos.application.indicators import (
    Acceptance,
    Adherence,
    DeliveryStats,
    NotificationLoad,
    RitualCost,
    UsageFrequency,
    Window,
)
from lifeos.web.indicators_routes import CACHE_TTL_S, ReportCache, serialize
from tests.web.conftest import Web

pytestmark = pytest.mark.integration


def test_returns_the_report_with_unknowns_as_null(web: Web) -> None:
    web.login()
    response = web.client.get("/api/indicadores", params={"dias": 7})
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["adherence"]["percent"] is None
    assert data["notifications"]["per_active_day"] is None
    assert data["costs"] == []
    assert (data["streak"], data["focus_max_minutes"]) == (0, 240)
    assert {item["name"] for item in data["not_measured"]} >= {"Utilidad y nivel de estrés"}
    assert data["end"] == "2026-11-01"  # NOW en UTC ya es 1 de noviembre en Lima


@pytest.mark.parametrize("dias", [0, 91])
def test_rejects_window_out_of_range(web: Web, dias: int) -> None:
    web.login()
    assert web.client.get("/api/indicadores", params={"dias": dias}).status_code == 422


def test_missing_horario_is_reported_as_an_error(web: Web) -> None:
    web.login()
    (web.vault / "_sistema" / "horario.md").unlink()
    response = web.client.get("/api/indicadores")
    assert response.status_code >= 400
    assert "horario" in response.text


def test_reuses_the_report_for_a_few_seconds(web: Web) -> None:
    web.login()
    first = web.client.get("/api/indicadores", params={"dias": 7}).json()["data"]
    web.write("07-Cotidiano/t.md", "- [x] Nueva \U0001f4c5 2026-11-01\n")
    again = web.client.get("/api/indicadores", params={"dias": 7}).json()["data"]
    assert again == first  # dentro del TTL: sin volver a leer la bóveda
    other = web.client.get("/api/indicadores", params={"dias": 8}).json()["data"]
    assert other["deliveries"]["completed"] == first["deliveries"]["completed"] + 1


def test_cache_expires() -> None:
    now = [100.0]
    cache = ReportCache(clock=lambda: now[0])
    cache.put(7, {"x": 1})
    assert cache.get(7) == {"x": 1}
    now[0] += CACHE_TTL_S
    assert cache.get(7) is None


def test_serializes_costs_and_notification_kinds_as_text() -> None:
    report = IndicatorReport(
        window=Window.ending(date(2026, 11, 2), days=7),
        deliveries=DeliveryStats(0, 0, 0, None),
        adherence=Adherence(0, 0, None),
        streak=0,
        focus_max_minutes=240,
        usage=UsageFrequency(0, 0.0),
        acceptance=Acceptance(0, 0, 0, None),
        costs=(RitualCost("jefe", "propuesta", 2, 0, 800, 1200, Decimal("0.03"), 1),),
        notifications=NotificationLoad(1, 0, 0, 1, 1, 1.0, (("briefing", 1),)),
        not_measured=(),
        quality=DataQuality(0, 0, 0, 0),
    )
    data = serialize(report)
    assert data["costs"] == [
        {
            "agent": "jefe",
            "ritual": "propuesta",
            "calls": 2,
            "errors": 0,
            "latency_median_ms": 800,
            "latency_p95_ms": 1200,
            "cost_estimated_usd": "0.03",
            "cost_unknown": 1,
        }
    ]
    assert data["notifications"]["by_kind"] == [{"kind": "briefing", "count": 1}]  # type: ignore[index]
