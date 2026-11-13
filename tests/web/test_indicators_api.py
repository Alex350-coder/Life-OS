"""`GET /api/indicadores` (F9-T03) sobre la app real y una bóveda inventada."""

import pytest

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
