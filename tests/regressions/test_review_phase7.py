"""Regresiones de la revisión de la Fase 7 (security-reviewer, code-reviewer, python-reviewer,
silent-failure-hunter y tdd-guide). Cada prueba fija un hallazgo corregido. Datos inventados."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from lifeos.adapters.google_access import GoogleCalendarAccess
from lifeos.adapters.google_calendar import GoogleBusyReader, GoogleCalendarWriter
from lifeos.adapters.google_http import GoogleHttp
from lifeos.adapters.google_oauth import GoogleOAuth, OAuthEndpoints
from lifeos.adapters.google_tokens import Grant, StoredToken, TokenStore
from lifeos.adapters.secrets import Secret
from lifeos.application.calendar_tools import CalendarTools, PartialCalendarSyncError
from lifeos.application.tools import ActorContext
from lifeos.bootstrap import Settings, _tokens_dir
from lifeos.domain.calendar_sync import PublishedEntry, desired_entries, plan_sync
from lifeos.ports.calendar import CalendarAuthError, CalendarError
from lifeos.ports.logs import AuditResult
from tests import config_samples as samples
from tests.calendar_fakes import FakeCalendarWriter
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import MON, NOW, TUE, WEEK, at, work
from tests.stub_http import Reply, StubServer

pytestmark = [pytest.mark.integration, pytest.mark.security]
CLIENT_ID = "123-prueba.apps.googleusercontent.com"
REFRESH = "1//refresh-de-prueba"
WHEN = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
JEFE = ActorContext("jefe", "c-jefe")


def reply(data: object, status: int = 200) -> Reply:
    return Reply(status=status, body=json.dumps(data).encode())


def oauth(stub: StubServer) -> GoogleOAuth:
    base = stub.base_url.removesuffix("/v1")
    return GoogleOAuth(
        CLIENT_ID,
        None,
        endpoints=OAuthEndpoints(f"{base}/auth", f"{base}/token", f"{base}/revoke"),
        http=GoogleHttp(loopback=True),
    )


def stored(grant: Grant) -> StoredToken:
    return StoredToken(grant, Secret(REFRESH), grant.scope, WHEN)


@dataclass
class OneToken:
    calls: list[str] = field(default_factory=list)

    def get(self) -> Secret:
        return Secret("ya29.x")

    def invalidate(self) -> None:
        self.calls.append("invalidate")


# --- OAuth (silent-failure-hunter HIGH/MEDIUM) ----------------------------------------------------


def test_unrevoked_extra_scope_tells_the_owner_where_to_remove_it(stub_server: StubServer) -> None:
    extra = f"{Grant.LECTURA.scope} https://www.googleapis.com/auth/calendar"
    stub_server.replies += [
        reply({"access_token": "a", "refresh_token": REFRESH, "scope": extra}),
        reply({}, 503),  # la revocación falla
    ]
    with pytest.raises(CalendarAuthError, match=r"myaccount.google.com/permissions"):
        oauth(stub_server).exchange(Grant.LECTURA, "c", "http://127.0.0.1:1/", "v", now=WHEN)


def test_extra_scope_without_refresh_token_revokes_the_access_token(
    stub_server: StubServer,
) -> None:
    stub_server.replies += [
        reply({"access_token": "ya29.solo", "scope": "https://www.googleapis.com/auth/calendar"}),
        reply({}),
    ]
    with pytest.raises(CalendarAuthError, match="permisos distintos"):
        oauth(stub_server).exchange(Grant.LECTURA, "c", "http://127.0.0.1:1/", "v", now=WHEN)
    assert b"token=ya29.solo" in stub_server.received[-1].body


def test_right_scope_without_refresh_token_is_an_error(stub_server: StubServer) -> None:
    stub_server.replies.append(reply({"access_token": "a", "scope": Grant.LECTURA.scope}))
    with pytest.raises(CalendarError, match="refresh_token"):
        oauth(stub_server).exchange(Grant.LECTURA, "c", "http://127.0.0.1:1/", "v", now=WHEN)


def test_refresh_with_other_scope_is_rejected(stub_server: StubServer) -> None:
    stub_server.replies.append(
        reply({"access_token": "a", "expires_in": 3600, "scope": Grant.ESCRITURA.scope})
    )
    with pytest.raises(CalendarAuthError):
        oauth(stub_server).refresh(stored(Grant.LECTURA))


@pytest.mark.parametrize("expires", [True, 0, -5, 10**9, "3600", None])
def test_refresh_with_invalid_expiry_is_rejected(stub_server: StubServer, expires: object) -> None:
    stub_server.replies.append(reply({"access_token": "a", "expires_in": expires}))
    with pytest.raises(CalendarError, match="caducidad"):
        oauth(stub_server).refresh(stored(Grant.LECTURA))


# --- HTTP (python-reviewer HIGH) -----------------------------------------------------------------


def test_cut_error_body_still_reports_the_http_status(stub_server: StubServer) -> None:
    # Content-Length falso: leer el cuerpo del error corta la conexión a mitad.
    stub_server.replies.append(Reply(status=500, body=b'{"error":', declared_length=4000))
    reader = GoogleBusyReader(
        OneToken(), base_url=stub_server.base_url, http=GoogleHttp(loopback=True)
    )
    with pytest.raises(CalendarError, match="500"):
        reader.busy(WHEN, WHEN + timedelta(days=1))


# --- Acceso (silent-failure-hunter MEDIUM) -------------------------------------------------------


def _access(stub: StubServer, tmp_path: Path) -> tuple[GoogleCalendarAccess, TokenStore]:
    store = TokenStore(tmp_path)
    return GoogleCalendarAccess(oauth(stub), store, now=lambda: WHEN), store


def test_damaged_token_file_does_not_hide_the_other_permission(
    stub_server: StubServer, tmp_path: Path
) -> None:
    access, store = _access(stub_server, tmp_path)
    store.save(stored(Grant.ESCRITURA))
    (tmp_path / "google-lectura.json").write_text("{dañado", encoding="utf-8")
    lectura, escritura = access.status()
    assert not lectura.connected and lectura.problem and "dañado" in lectura.problem
    assert escritura.connected and escritura.problem is None


def test_damaged_token_file_does_not_block_revoking_the_other(
    stub_server: StubServer, tmp_path: Path
) -> None:
    access, store = _access(stub_server, tmp_path)
    store.save(stored(Grant.ESCRITURA))
    (tmp_path / "google-lectura.json").write_text("{dañado", encoding="utf-8")
    stub_server.replies.append(reply({}))
    with pytest.raises(CalendarError, match="Ya revocado: escritura"):
        access.revoke()
    assert not store.has(Grant.ESCRITURA)


def test_failed_revocation_keeps_the_local_token_to_retry(
    stub_server: StubServer, tmp_path: Path
) -> None:
    access, store = _access(stub_server, tmp_path)
    store.save(stored(Grant.LECTURA))
    stub_server.replies.append(reply({}, 503))
    with pytest.raises(CalendarError, match="se conserva"):
        access.revoke()
    assert store.has(Grant.LECTURA)


# --- Calendario (security-reviewer M1, silent-failure-hunter MEDIUM) -----------------------------


def test_404_on_a_later_page_is_an_error_and_keeps_the_calendar(
    stub_server: StubServer, tmp_path: Path
) -> None:
    store = TokenStore(tmp_path)
    store.save_calendar_id("cal@group.calendar.google.com")
    client = GoogleCalendarWriter(
        OneToken(),
        store,
        time_zone="UTC",
        base_url=stub_server.base_url,
        http=GoogleHttp(loopback=True),
    )
    stub_server.replies += [reply({"items": [], "nextPageToken": "p2"}), reply({}, 404)]
    with pytest.raises(CalendarError):
        client.list_entries(WEEK.render(), WHEN, WHEN + timedelta(days=7))
    assert store.calendar_id() == "cal@group.calendar.google.com"


# --- Herramientas (silent-failure-hunter MEDIUM, tdd-guide) ---------------------------------------


def _tools(writer: FakeCalendarWriter) -> tuple[CalendarTools, MemoryAuditLog]:
    audit = MemoryAuditLog()
    return CalendarTools(samples.effective(), audit, FakeClock(NOW), writer=writer), audit


def test_partial_failure_keeps_the_real_cause_in_the_message() -> None:
    entries = desired_entries(WEEK, (work(MON, "14:00–15:00", "t-aaaaaa"),))
    stale = PublishedEntry("old", "f" * 32, "x", work(TUE, "09:00–10:00", "t-bbbbbb").interval)
    writer = FakeCalendarWriter(fail_after=1)
    tools, _ = _tools(writer)
    with pytest.raises(PartialCalendarSyncError, match="no respondió") as error:
        tools.apply(JEFE, WEEK.render(), plan_sync(entries, (stale,)))
    assert error.value.done == 1


def test_first_step_failure_is_the_original_error() -> None:
    writer = FakeCalendarWriter(fail_after=0)
    tools, audit = _tools(writer)
    entries = desired_entries(WEEK, (work(MON, "14:00–15:00", "t-aaaaaa"),))
    with pytest.raises(CalendarError) as error:
        tools.apply(JEFE, WEEK.render(), plan_sync(entries, ()))
    assert not isinstance(error.value, PartialCalendarSyncError)
    assert audit.records[-1].result is AuditResult.ERROR


def test_unexpected_reader_bug_is_still_audited() -> None:
    class Broken:
        def busy(self, start: datetime, end: datetime) -> tuple[()]:
            raise RuntimeError("fallo inesperado")

    audit = MemoryAuditLog()
    tools = CalendarTools(samples.effective(), audit, FakeClock(NOW), reader=Broken())
    with pytest.raises(RuntimeError):
        tools.busy(JEFE, WHEN, WHEN + timedelta(days=1))
    assert [(r.action, r.result) for r in audit.records] == [("leer_calendario", AuditResult.ERROR)]


# --- Lo pasado queda congelado (code-reviewer M1) ------------------------------------------------


def test_finished_blocks_are_neither_created_nor_deleted() -> None:
    past = work(MON, "14:00–15:00", "t-aaaaaa")
    future = work(TUE, "09:00–10:00", "t-bbbbbb")
    entries = desired_entries(WEEK, (past, future))
    old_published = PublishedEntry("p1", "a" * 32, "antiguo", past.interval)
    sync = plan_sync(entries, (old_published,), not_before=at(MON, "16:00"))
    assert [e.interval for e in sync.create] == [future.interval]
    assert sync.delete == ()  # lo publicado antes de "ahora" es historia
    assert sync.unchanged == 1


# --- Tokens fuera de OneDrive (security-reviewer M3) ---------------------------------------------


def test_tokens_live_outside_the_project_by_default(tmp_path: Path) -> None:
    settings = Settings.from_env(tmp_path, {})
    assert _tokens_dir(settings) == Path.home() / ".lifeos"
    assert tmp_path not in _tokens_dir(settings).parents


def test_token_store_rejects_oversized_files_and_failed_saves(tmp_path: Path) -> None:
    store = TokenStore(tmp_path)
    (tmp_path / "google-lectura.json").write_bytes(b"{" + b" " * 20_000 + b"}")
    with pytest.raises(CalendarError, match="dañado"):
        store.load(Grant.LECTURA)
    blocked = TokenStore(tmp_path / "google-lectura.json")  # una "carpeta" que es un archivo
    with pytest.raises(CalendarError, match="no se pudo guardar"):
        blocked.save(stored(Grant.ESCRITURA))
