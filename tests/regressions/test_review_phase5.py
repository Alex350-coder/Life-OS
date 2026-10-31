"""Regresiones de la revisión de la Fase 5 (fastapi-reviewer, security-reviewer, python-reviewer
y silent-failure-hunter). Cada prueba reproduce un hallazgo antes de corregirlo."""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifeos.adapters.jsonl_logs import JsonlAuditLog, JsonlAuditReader, JsonlUsageReader
from lifeos.application.closing import ClosingIncompleteError
from lifeos.application.history import recent_changes
from lifeos.application.plan_publication import PartialPublicationError
from lifeos.application.tasks import MAX_UPCOMING_DAYS
from lifeos.ports.logs import AuditRecord, AuditResult, AuditScan, LogWriteError
from lifeos.ports.vault import VaultIOError, VaultPath
from lifeos.web import routes
from lifeos.web.security import API_RATE_LIMIT, AUTH_RATE_LIMIT, SessionStore
from tests.web.conftest import ORIGIN, TOKEN, Monotonic, Web, build_web
from tests.web.conftest import web as web  # fixture compartida

pytestmark = [pytest.mark.security, pytest.mark.integration]

NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


@pytest.fixture
def lenient(tmp_path: Path) -> Iterator[Web]:
    """Cliente que no relanza las excepciones del servidor: se ve la respuesta 500 real."""
    harness = build_web(tmp_path)
    harness.client = TestClient(harness.client.app, base_url=ORIGIN, raise_server_exceptions=False)
    with harness.client:
        yield harness


# --- HIGH: estáticos con rutas UNC o absolutas (fastapi-reviewer) ------------------------------


@pytest.mark.parametrize(
    "path",
    ["/%5C%5Cevil.example%5Cshare%5Ca", "///evil.example/share/a", "/C:/Windows/win.ini", "/%00"],
)
def test_static_never_touches_paths_outside_dist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><title>Life OS</title>", encoding="utf-8")
    touched: list[str] = []
    original = Path.resolve

    def spy(self: Path, strict: bool = False) -> Path:
        touched.append(str(self))
        return original(self, strict)

    harness = build_web(tmp_path, dist=dist)
    monkeypatch.setattr(Path, "resolve", spy)
    with TestClient(harness.client.app, base_url=ORIGIN, raise_server_exceptions=False) as client:
        response = client.get(path)
    assert response.status_code in {200, 404}
    assert all(t.startswith(str(dist)) or t == str(dist) for t in touched), touched


def test_static_serves_files_inside_dist(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>Life OS</title>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    harness = build_web(tmp_path, dist=dist)
    with TestClient(harness.client.app, base_url=ORIGIN) as client:
        assert client.get("/assets/app.js").text == "export {};"
        assert "Life OS" in client.get("/semana").text  # ruta de la SPA
        assert client.head("/").status_code == 200


# --- HIGH: 500 sin cabeceras ni sobre (fastapi-reviewer, silent-failure-hunter) ------------


def test_unexpected_error_is_an_enveloped_500_with_security_headers(
    lenient: Web, monkeypatch: pytest.MonkeyPatch
) -> None:
    lenient.login()

    def boom(*_: object, **__: object) -> object:
        raise RuntimeError("detalle interno con /ruta/secreta")

    monkeypatch.setattr(routes, "query_tasks", boom)
    response = lenient.client.get("/api/tareas")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "interno"
    assert "secreta" not in response.text
    assert "content-security-policy" in response.headers
    assert response.headers["cache-control"] == "no-store"


# --- HIGH: errores de E/S y escrituras parciales no son un "rechazo" (silent-failure-hunter) --


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (VaultIOError("no se pudo leer la bóveda"), 503, "io"),
        (LogWriteError("no se pudo leer audit-2026-11.jsonl"), 503, "io"),
        (
            PartialPublicationError((VaultPath("Semanal/2026-W45.md"),), OSError("disco")),
            500,
            "escritura_parcial",
        ),
        (
            ClosingIncompleteError(("01-Universidad/redes.md",), OSError("disco")),
            500,
            "escritura_parcial",
        ),
    ],
)
def test_io_and_partial_writes_have_their_own_codes(
    lenient: Web, monkeypatch: pytest.MonkeyPatch, error: Exception, status: int, code: str
) -> None:
    lenient.login()

    def fail(*_: object, **__: object) -> object:
        raise error

    monkeypatch.setattr(routes, "query_tasks", fail)
    response = lenient.client.get("/api/tareas")
    assert (response.status_code, response.json()["error"]["code"]) == (status, code)
    if code == "escritura_parcial":
        assert response.json()["data"]["written"]


# --- HIGH: un byte no UTF-8 en los logs rompía historial, agentes y uso -----------------------


def test_non_utf8_log_lines_are_counted_as_malformed(tmp_path: Path) -> None:
    log = JsonlAuditLog(tmp_path)
    record = AuditRecord(NOW, "c1", "coach", "anexar_checkin", "Diario/x.md", AuditResult.OK)
    log.append(record)
    with (tmp_path / "audit-2026-11.jsonl").open("ab") as handle:
        handle.write(b'{"bad": "\xff\xfe"}\n')
    scan = JsonlAuditReader(tmp_path).read_month(2026, 11)
    assert scan.records == (record,) and scan.malformed == 1
    (tmp_path / "usage-2026-11.jsonl").write_bytes(b"\xff\n")
    assert JsonlUsageReader(tmp_path).read_month(2026, 11).malformed == 1


# --- MEDIUM: horas con desfase y sufijo Z (python-reviewer) ------------------------------------


def test_timestamp_with_offset_and_z_is_malformed(tmp_path: Path) -> None:
    JsonlAuditLog(tmp_path).append(
        AuditRecord(NOW, "c1", "coach", "anexar_checkin", "Diario/x.md", AuditResult.OK)
    )
    path = tmp_path / "audit-2026-11.jsonl"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["ts"] = "2026-11-02T15:00:00+05:00Z"
    path.write_text(json.dumps(data) + "\n", encoding="utf-8")
    assert JsonlAuditReader(tmp_path).read_month(2026, 11).malformed == 1


# --- MEDIUM: dos intentos con la misma clave y un solo resultado (python-reviewer) ------------


class _Reader:
    def __init__(self, records: tuple[AuditRecord, ...]) -> None:
        self.records = records

    def read_month(self, year: int, month: int) -> AuditScan:
        return AuditScan(self.records if (year, month) == (2026, 11) else (), 0, 1)


def test_second_attempt_without_result_stays_visible() -> None:
    def rec(minutes: int, result: AuditResult) -> AuditRecord:
        return AuditRecord(
            NOW - timedelta(minutes=minutes), "c1", "jefe", "publicar_plan", "Diario/x.md", result
        )

    records = (rec(5, AuditResult.INTENTO), rec(4, AuditResult.OK), rec(3, AuditResult.INTENTO))
    entries = recent_changes(_Reader(records), NOW).entries
    assert [(e.result, e.completed) for e in entries] == [
        (AuditResult.INTENTO, False),
        (AuditResult.OK, True),
    ]


# --- MEDIUM: booleanos permisivos activaban la IA (fastapi-reviewer) ------------------------


@pytest.mark.parametrize("value", ["yes", "on", "1", 1, "true"])
def test_ia_flag_must_be_a_json_boolean(web: Web, value: object) -> None:
    csrf = web.login()
    response = web.post("/api/plan/propuesta", {"semana": "2026-W45", "ia": value}, csrf)
    assert response.status_code == 422  # type: ignore[attr-defined]


# --- MEDIUM: límite de tasa agotable desde otra web (security-reviewer, fastapi-reviewer) -----


def test_unauthenticated_api_requests_do_not_lock_out_the_owner(web: Web) -> None:
    for _ in range(API_RATE_LIMIT + 5):
        web.client.get("/api/session", headers={"Cookie": ""})
    web.client.cookies.clear()
    web.login()
    assert web.client.get("/api/session").status_code == 200


@pytest.mark.parametrize("site", ["cross-site", "same-site"])
def test_cross_site_fetches_are_refused_before_counting(web: Web, site: str) -> None:
    for _ in range(AUTH_RATE_LIMIT + 5):
        response = web.client.get(
            "/auth", params={"token": "x"}, headers={"Sec-Fetch-Site": site}, follow_redirects=False
        )
        assert response.status_code == 403
    ok = web.client.get(
        "/auth", params={"token": TOKEN}, headers={"Sec-Fetch-Site": "none"}, follow_redirects=False
    )
    assert ok.status_code == 303
    assert web.client.get("/api/tareas", headers={"Sec-Fetch-Site": site}).status_code == 403


def test_ai_requests_have_their_own_tight_limit(web: Web) -> None:
    csrf = web.login()
    codes = [
        web.post("/api/plan/propuesta", {"semana": "2026-W45", "ia": True}, csrf).status_code  # type: ignore[attr-defined]
        for _ in range(routes.AI_RATE_LIMIT + 1)
    ]
    assert codes[-1] == 429
    assert set(codes[:-1]) == {503}  # sin clave: error de configuración, pero cuenta


def test_request_body_size_is_capped(web: Web) -> None:
    csrf = web.login()
    huge = "x" * 70_000
    response = web.client.post(
        "/api/capturas",
        content=json.dumps({"texto": huge}),
        headers={"X-CSRF-Token": csrf, "Origin": ORIGIN, "Content-Type": "application/json"},
    )
    assert response.status_code == 413


# --- MEDIUM y LOW: sobre común, mensajes y detalles --------------------------------------------


def test_method_not_allowed_uses_the_envelope(web: Web) -> None:
    csrf = web.login()
    response = web.post("/api/hoy", {}, csrf)
    assert response.status_code == 405  # type: ignore[attr-defined]
    assert response.json()["ok"] is False  # type: ignore[attr-defined]


def test_domain_errors_do_not_echo_the_value(web: Web) -> None:
    web.login()
    response = web.client.get("/api/semana", params={"semana": "<b>x</b>"})
    assert response.status_code == 422
    assert "<b>" not in response.text


def test_logout_needs_no_body(web: Web) -> None:
    csrf = web.login()
    response = web.client.post("/api/logout", headers={"X-CSRF-Token": csrf, "Origin": ORIGIN})
    assert response.status_code == 204


def test_expired_sessions_are_purged_when_creating() -> None:
    clock = Monotonic()
    store = SessionStore(clock, ttl_s=10)
    store.create()
    clock.advance(11)
    store.create()
    assert store.active() == 1


def test_upcoming_route_uses_the_shared_bound(web: Web) -> None:
    web.login()
    over = web.client.get("/api/tareas/proximas", params={"dias": MAX_UPCOMING_DAYS + 1})
    assert over.status_code == 422
