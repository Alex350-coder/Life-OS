"""Google Calendar en la web, solo lectura (F10-T04, DT-27, SEC-11, SEC-13).

`GET /api/calendario` dice qué permisos están conectados sin llamar a Google.
`GET /api/calendario/disponibilidad` lee los intervalos ocupados y los choques con el plan,
contra un Google falso local. La web no tiene ninguna ruta que escriba en Google.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from lifeos.adapters.google_tokens import Grant, StoredToken, TokenStore
from lifeos.adapters.secrets import Secret
from lifeos.web.calendar_routes import CACHE_TTL_S, MAX_CACHED_WEEKS, AvailabilityCache
from tests.fake_google import READ, FakeGoogle, fake_google  # noqa: F401 - fixture
from tests.web.conftest import ORIGIN, Web, build_web

pytestmark = [pytest.mark.integration, pytest.mark.security]

CLIENT_ID = "123-prueba.apps.googleusercontent.com"
WEEK = "2026-W45"
MORNING = {"start": "2026-11-02T13:00:00Z", "end": "2026-11-02T15:00:00Z"}


def _calendar_web(tmp_path: Path, google: FakeGoogle, *, read_token: bool) -> Web:
    state = tmp_path / "state"
    if read_token:
        google.refresh["1//r-lectura"] = READ
        TokenStore(state).save(
            StoredToken(
                Grant.LECTURA, Secret("1//r-lectura"), READ, datetime(2026, 10, 30, tzinfo=UTC)
            )
        )
    return build_web(
        tmp_path,
        extra_env={
            "LIFEOS_STATE_DIR": str(state),
            "LIFEOS_TOKENS_DIR": str(state),
            "GOOGLE_CLIENT_ID": CLIENT_ID,
            "LIFEOS_GOOGLE_OAUTH": google.oauth_base,
            "LIFEOS_GOOGLE_API": google.api_base,
        },
    )


def test_status_without_client_id_says_not_configured(web: Web) -> None:
    web.login()

    data = web.client.get("/api/calendario").json()["data"]

    assert data == {"configured": False, "grants": [], "can_read": False, "problem": None}


def test_status_lists_each_grant_without_calling_google(
    tmp_path: Path,
    fake_google: FakeGoogle,  # noqa: F811
) -> None:
    harness = _calendar_web(tmp_path, fake_google, read_token=True)
    with harness.client:
        harness.login()
        fake_google.revoked = True  # si se llamara a Google, fallaría

        data = harness.client.get("/api/calendario").json()["data"]

    assert data["configured"] and data["can_read"]
    grants = {g["grant"]: g for g in data["grants"]}
    assert grants["lectura"]["connected"] and grants["lectura"]["obtained"].startswith("2026-10-30")
    assert not grants["escritura"]["connected"]
    assert "refresh" not in str(data) and "1//" not in str(data)  # sin secretos


def test_availability_shows_busy_intervals_and_plan_clashes(
    tmp_path: Path,
    fake_google: FakeGoogle,  # noqa: F811
) -> None:
    fake_google.busy.append(MORNING)
    harness = _calendar_web(tmp_path, fake_google, read_token=True)
    with harness.client:
        csrf = harness.login()
        proposed = harness.post("/api/plan/propuesta", {"semana": WEEK}, csrf)
        assert proposed.status_code == 200, proposed.text  # type: ignore[attr-defined]

        response = harness.client.get("/api/calendario/disponibilidad", params={"semana": WEEK})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["week"] == WEEK
    (busy,) = data["busy"]
    assert busy["start"] == "2026-11-02T13:00:00+00:00"
    assert busy["confidence"] in {"alta", "media"}
    assert data["plan_path"] is not None
    assert data["clashes"] == []  # el plan nuevo ya respeta lo ocupado


def test_availability_without_read_grant_is_a_clear_error(
    tmp_path: Path,
    fake_google: FakeGoogle,  # noqa: F811
) -> None:
    harness = _calendar_web(tmp_path, fake_google, read_token=False)
    with harness.client:
        harness.login()

        response = harness.client.get("/api/calendario/disponibilidad")

    assert response.status_code == 409
    assert "conectar --lectura" in response.json()["error"]["message"]


@pytest.mark.parametrize("path", ["/api/calendario", "/api/calendario/disponibilidad"])
def test_calendar_routes_require_a_session(web: Web, path: str) -> None:
    assert web.client.get(path).status_code == 401


def test_invalid_week_is_rejected(web: Web) -> None:
    web.login()
    assert web.client.get("/api/calendario/disponibilidad", params={"semana": "x"}).status_code in (
        400,
        422,
    )


@pytest.mark.parametrize("method", ["post", "put", "delete"])
def test_web_cannot_write_to_google(web: Web, method: str) -> None:
    csrf = web.login()
    headers = {"X-CSRF-Token": csrf, "Origin": ORIGIN}
    for path in ("/api/calendario", "/api/calendario/publicar", "/api/calendario/conectar"):
        response = web.client.request(method, path, headers=headers)
        assert response.status_code in (404, 405), (method, path)


def test_blank_client_id_counts_as_not_configured(tmp_path: Path) -> None:
    harness = build_web(tmp_path, extra_env={"GOOGLE_CLIENT_ID": "   "})
    with harness.client:
        harness.login()
        data = harness.client.get("/api/calendario").json()["data"]
    assert data["configured"] is False


def test_rejected_client_config_is_shown_as_a_problem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lifeos.ports.config import ConfigRejectedError
    from lifeos.web import calendar_routes

    def rejected(_settings: object) -> object:
        raise ConfigRejectedError("GOOGLE_CLIENT_ID", ("formato no válido",))

    monkeypatch.setattr(calendar_routes, "calendar_access", rejected)
    harness = build_web(tmp_path, extra_env={"GOOGLE_CLIENT_ID": CLIENT_ID})
    with harness.client:
        harness.login()
        response = harness.client.get("/api/calendario")

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["configured"] is True
    assert "formato no válido" in data["problem"]


def test_cache_expires_and_keeps_at_most_a_few_weeks() -> None:
    now = [0.0]
    cache = AvailabilityCache(clock=lambda: now[0])
    for n in range(MAX_CACHED_WEEKS + 5):
        cache.put(f"2026-W{n:02d}", {"n": n})
    assert len(cache.entries) == MAX_CACHED_WEEKS
    assert cache.get("2026-W00") is None  # la más antigua salió
    assert cache.get(f"2026-W{MAX_CACHED_WEEKS + 4:02d}") == {"n": MAX_CACHED_WEEKS + 4}
    now[0] = CACHE_TTL_S
    assert cache.get(f"2026-W{MAX_CACHED_WEEKS + 4:02d}") is None
    cache.put("2026-W50", {})
    assert list(cache.entries) == ["2026-W50"]  # las caducadas se limpian
