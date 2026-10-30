"""SEC-11 (ADR-0013): API local con autenticación, CSRF y Host/Origin; sin CORS ni exposición.

Una petición sin sesión → 401; de un Host u Origin no permitido → rechazada; un método que cambia
estado sin token CSRF válido → 403. Todo contra la aplicación real.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifeos.web.security import (
    API_RATE_LIMIT,
    AUTH_RATE_LIMIT,
    LAUNCH_TOKEN_TTL_S,
    SESSION_COOKIE,
    SESSION_TTL_S,
    LaunchToken,
    RateLimiter,
    SessionStore,
    check_bind_host,
)
from tests.web.conftest import ORIGIN, PORT, TOKEN, Monotonic, Web

pytestmark = [pytest.mark.security, pytest.mark.integration]

CAPTURE = {"texto": "Idea de prueba"}


# --- Sin sesión ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path", ["/api/session", "/api/hoy", "/api/semana", "/api/tareas", "/api/historial"]
)
def test_api_without_session_is_401(web: Web, path: str) -> None:
    response = web.client.get(path)
    assert response.status_code == 401
    assert response.json() == {
        "ok": False,
        "error": {"code": "sin_sesion", "message": "abre la URL que muestra lifeos-web al arrancar"},
    }


def test_post_without_session_is_401_and_writes_nothing(web: Web) -> None:
    before = sorted(p.name for p in web.vault.rglob("*.md"))
    response = web.client.post("/api/capturas", json=CAPTURE, headers={"Origin": ORIGIN})
    assert response.status_code == 401
    assert sorted(p.name for p in web.vault.rglob("*.md")) == before


def test_a_forged_session_cookie_is_401(web: Web) -> None:
    web.client.cookies.set(SESSION_COOKIE, "inventada")
    assert web.client.get("/api/tareas").status_code == 401


# --- Token de arranque -----------------------------------------------------------------------


def test_launch_token_opens_a_strict_http_only_session(web: Web) -> None:
    response = web.client.get("/auth", params={"token": TOKEN}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE}=")
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/" in cookie
    assert TOKEN not in cookie


def test_wrong_token_is_rejected_without_cookie(web: Web) -> None:
    response = web.client.get("/auth", params={"token": "otro"}, follow_redirects=False)
    assert response.status_code == 401
    assert "set-cookie" not in response.headers


def test_missing_token_is_rejected(web: Web) -> None:
    assert web.client.get("/auth", follow_redirects=False).status_code == 401


def test_launch_token_is_single_use(web: Web) -> None:
    web.login()
    again = web.client.get("/auth", params={"token": TOKEN}, follow_redirects=False)
    assert again.status_code == 401


def test_launch_token_expires(web: Web) -> None:
    web.monotonic.advance(LAUNCH_TOKEN_TTL_S + 1)
    response = web.client.get("/auth", params={"token": TOKEN}, follow_redirects=False)
    assert response.status_code == 401


def test_session_endpoint_never_returns_the_session_or_launch_token(web: Web) -> None:
    web.login()
    body = web.client.get("/api/session").text
    assert TOKEN not in body
    assert web.client.cookies[SESSION_COOKIE] not in body


# --- CSRF ------------------------------------------------------------------------------------


def test_post_without_csrf_header_is_403(web: Web) -> None:
    web.login()
    response = web.client.post("/api/capturas", json=CAPTURE, headers={"Origin": ORIGIN})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf"


def test_post_with_wrong_csrf_is_403(web: Web) -> None:
    web.login()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": "falso"}
    assert web.client.post("/api/capturas", json=CAPTURE, headers=headers).status_code == 403


def test_post_with_session_and_csrf_is_accepted(web: Web) -> None:
    csrf = web.login()
    response = web.post("/api/capturas", CAPTURE, csrf)
    assert response.status_code == 201  # type: ignore[attr-defined]


# --- Host y Origin ---------------------------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example", f"evil.example:{PORT}", "127.0.0.1:9999"])
def test_foreign_host_is_400_dns_rebinding(web: Web, host: str) -> None:
    csrf = web.login()
    response = web.client.get("/api/tareas", headers={"Host": host})
    assert response.status_code == 400
    posted = web.client.post(
        "/api/capturas", json=CAPTURE, headers={"Host": host, "X-CSRF-Token": csrf}
    )
    assert posted.status_code == 400


def test_localhost_host_is_allowed(web: Web) -> None:
    web.login()
    response = web.client.get("/api/tareas", headers={"Host": f"localhost:{PORT}"})
    assert response.status_code == 200


@pytest.mark.parametrize("origin", ["http://evil.example", "null", f"http://127.0.0.1:{PORT + 1}"])
def test_foreign_origin_is_403_even_with_session(web: Web, origin: str) -> None:
    csrf = web.login()
    headers = {"Origin": origin, "X-CSRF-Token": csrf}
    assert web.client.post("/api/capturas", json=CAPTURE, headers=headers).status_code == 403
    assert web.client.get("/api/tareas", headers={"Origin": origin}).status_code == 403


def test_state_change_without_origin_is_403(web: Web) -> None:
    csrf = web.login()
    headers = {"X-CSRF-Token": csrf}
    assert web.client.post("/api/capturas", json=CAPTURE, headers=headers).status_code == 403


def test_no_cors_headers_and_preflight_is_refused(web: Web) -> None:
    preflight = web.client.options(
        "/api/capturas",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 403
    assert "access-control-allow-origin" not in preflight.headers
    web.login()
    assert "access-control-allow-origin" not in web.client.get("/api/tareas").headers


# --- Cabeceras, sesión y tasa ----------------------------------------------------------------


def test_security_headers_on_every_response(web: Web) -> None:
    for response in (web.client.get("/"), web.client.get("/api/tareas")):
        headers = response.headers
        assert "default-src 'self'" in headers["content-security-policy"]
        assert "frame-ancestors 'none'" in headers["content-security-policy"]
        assert "unsafe-inline" not in headers["content-security-policy"].split("style-src")[0]
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["x-frame-options"] == "DENY"
        assert headers["referrer-policy"] == "no-referrer"
    assert web.client.get("/api/tareas").headers["cache-control"] == "no-store"


def test_session_expires(web: Web) -> None:
    web.login()
    web.monotonic.advance(SESSION_TTL_S + 1)
    assert web.client.get("/api/tareas").status_code == 401


def test_logout_revokes_the_session(web: Web) -> None:
    csrf = web.login()
    assert web.post("/api/logout", {}, csrf).status_code == 204  # type: ignore[attr-defined]
    assert web.client.get("/api/tareas").status_code == 401


def test_auth_attempts_are_rate_limited(web: Web) -> None:
    codes = [
        web.client.get("/auth", params={"token": "x"}, follow_redirects=False).status_code
        for _ in range(AUTH_RATE_LIMIT + 1)
    ]
    assert codes[:-1] == [401] * AUTH_RATE_LIMIT
    last = web.client.get("/auth", params={"token": TOKEN}, follow_redirects=False)
    assert last.status_code == 429 and int(last.headers["retry-after"]) > 0


def test_api_requests_are_rate_limited(web: Web) -> None:
    web.login()
    codes = {web.client.get("/api/session").status_code for _ in range(API_RATE_LIMIT)}
    assert 429 in codes
    web.monotonic.advance(61)
    assert web.client.get("/api/session").status_code == 200


def test_api_docs_are_not_exposed(web: Web) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert web.client.get(path).status_code in {401, 404}


def test_launch_token_does_not_reach_the_logs(web: Web) -> None:
    csrf = web.login()
    web.post("/api/capturas", CAPTURE, csrf)
    logs = web.root / "logs"
    texts = [p.read_text(encoding="utf-8") for p in logs.glob("*.jsonl")] if logs.exists() else []
    assert all(TOKEN not in text for text in texts)


# --- Primitivas ------------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("host", ["0.0.0.0", "::", "192.168.1.10", "localhost", "", "::1"])  # noqa: S104
def test_only_loopback_ipv4_can_be_bound(host: str) -> None:
    with pytest.raises(ValueError, match=r"127\.0\.0\.1"):
        check_bind_host(host)


@pytest.mark.unit
def test_bind_to_loopback_is_accepted() -> None:
    check_bind_host("127.0.0.1")


@pytest.mark.unit
def test_generated_launch_token_is_long_and_random() -> None:
    clock = Monotonic()
    first, second = LaunchToken(clock), LaunchToken(clock)
    assert len(first.value) >= 43 and first.value != second.value


@pytest.mark.unit
def test_sessions_are_random_and_carry_their_own_csrf() -> None:
    store = SessionStore(Monotonic())
    a, b = store.create(), store.create()
    assert a.id != b.id and a.csrf != b.csrf and a.id != a.csrf
    assert store.get(a.id) == a
    store.revoke(a.id)
    assert store.get(a.id) is None and store.get(b.id) == b


@pytest.mark.unit
def test_rate_limiter_window_slides() -> None:
    clock = Monotonic()
    limiter = RateLimiter(clock, limit=2, window_s=60)
    assert limiter.allow("k") == (True, 0)
    assert limiter.allow("k") == (True, 0)
    allowed, retry = limiter.allow("k")
    assert not allowed and 0 < retry <= 60
    assert limiter.allow("otra")[0]
    clock.advance(61)
    assert limiter.allow("k") == (True, 0)


@pytest.mark.unit
def test_unbuilt_frontend_answers_with_instructions(tmp_path: Path) -> None:
    from tests.web.conftest import build_web

    harness = build_web(tmp_path)
    with TestClient(harness.client.app, base_url=ORIGIN) as client:
        response = client.get("/")
    assert response.status_code == 503
    assert "npm run build" in response.text
