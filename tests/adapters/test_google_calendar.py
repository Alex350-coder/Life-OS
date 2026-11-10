"""Google Calendar API contra un servidor local (ADR-0017, SEC-13). Datos inventados."""

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from lifeos.adapters.google_calendar import (
    KEY_PROPERTY,
    SOURCE,
    WEEK_PROPERTY,
    GoogleBusyReader,
    GoogleCalendarWriter,
)
from lifeos.adapters.google_http import GoogleHttp
from lifeos.adapters.google_tokens import TokenStore
from lifeos.adapters.secrets import Secret
from lifeos.domain.calendar_sync import desired_entries
from lifeos.domain.external import Confidence
from lifeos.ports.calendar import CalendarAuthError, CalendarError
from tests.planning_builders import MON, WEEK, work
from tests.stub_http import Reply, StubServer

pytestmark = [pytest.mark.integration, pytest.mark.security]
ACCESS = "ya29.access-de-prueba"
START = datetime(2026, 11, 2, 5, 0, tzinfo=UTC)
END = START + timedelta(days=7)
CAL_ID = "abc123@group.calendar.google.com"
ENTRY = desired_entries(WEEK, (work(MON, "14:00–15:00", "t-aaaaaa", "Informe"),))[0]


@dataclass
class FakeTokens:
    values: list[str] = field(default_factory=lambda: [ACCESS])
    invalidated: int = 0

    def get(self) -> Secret:
        return Secret(self.values[min(self.invalidated, len(self.values) - 1)])

    def invalidate(self) -> None:
        self.invalidated += 1


def reply(data: object, status: int = 200) -> Reply:
    return Reply(status=status, body=json.dumps(data).encode())


def reader(stub: StubServer, tokens: FakeTokens | None = None) -> GoogleBusyReader:
    return GoogleBusyReader(
        tokens or FakeTokens(), base_url=stub.base_url, http=GoogleHttp(loopback=True)
    )


def writer(stub: StubServer, tmp_path: Path) -> tuple[GoogleCalendarWriter, TokenStore]:
    store = TokenStore(tmp_path)
    client = GoogleCalendarWriter(
        FakeTokens(),
        store,
        time_zone="America/Lima",
        base_url=stub.base_url,
        http=GoogleHttp(loopback=True),
    )
    return client, store


def busy_reply(*spans: tuple[str, str]) -> Reply:
    busy = [{"start": a, "end": b} for a, b in spans]
    return reply({"calendars": {"primary": {"busy": busy}}})


# --- Lectura (freebusy) ---------------------------------------------------------------------------


def test_busy_reads_only_intervals_from_the_primary_calendar(stub_server: StubServer) -> None:
    stub_server.replies.append(busy_reply(("2026-11-02T19:00:00Z", "2026-11-02T20:00:00Z")))
    (item,) = reader(stub_server).busy(START, END)
    assert (item.source, item.confidence) == (SOURCE, Confidence.ALTA)
    assert item.interval.minutes == 60
    request = stub_server.received[0]
    assert (request.method, request.path) == ("POST", "/v1/freeBusy")
    assert json.loads(request.body)["items"] == [{"id": "primary"}]
    assert request.headers["Authorization"] == f"Bearer {ACCESS}"


def test_busy_skips_empty_intervals(stub_server: StubServer) -> None:
    stub_server.replies.append(busy_reply(("2026-11-02T19:00:00Z", "2026-11-02T19:00:00Z")))
    assert reader(stub_server).busy(START, END) == ()


@pytest.mark.parametrize(
    "data",
    [
        {"calendars": {}},
        {"calendars": {"primary": {"errors": [{"reason": "notFound"}]}}},
        {"calendars": {"primary": {"busy": [{"start": "ayer", "end": "hoy"}]}}},
        {"calendars": {"primary": {"busy": "no es lista"}}},
    ],
)
def test_malformed_availability_is_an_explicit_error(
    stub_server: StubServer, data: dict[str, object]
) -> None:
    stub_server.replies.append(reply(data))
    with pytest.raises(CalendarError):
        reader(stub_server).busy(START, END)


def test_expired_access_token_is_renewed_once(stub_server: StubServer) -> None:
    tokens = FakeTokens(["ya29.viejo", "ya29.nuevo"])
    stub_server.replies += [reply({}, 401), busy_reply()]
    assert reader(stub_server, tokens).busy(START, END) == ()
    assert tokens.invalidated == 1
    assert stub_server.received[1].headers["Authorization"] == "Bearer ya29.nuevo"


def test_insufficient_scope_is_an_authorization_error(stub_server: StubServer) -> None:
    error = {"error": {"errors": [{"reason": "insufficientPermissions"}]}}
    stub_server.replies.append(reply(error, 403))
    with pytest.raises(CalendarAuthError, match="insufficientPermissions"):
        reader(stub_server).busy(START, END)


def test_errors_never_reveal_the_access_token(stub_server: StubServer) -> None:
    stub_server.replies.append(reply({"error": {"status": "UNAVAILABLE"}}, 503))
    with pytest.raises(CalendarError) as error:
        reader(stub_server).busy(START, END)
    assert ACCESS not in str(error.value)
    assert "503" in str(error.value)


def test_naive_dates_are_rejected_before_calling_google(stub_server: StubServer) -> None:
    with pytest.raises(CalendarError):
        reader(stub_server).busy(START.replace(tzinfo=None), END)
    assert stub_server.received == []


# --- Escritura (calendario propio) ----------------------------------------------------------------


def event(event_id: str, *, key: str = ENTRY.key, status: str = "confirmed") -> dict[str, object]:
    return {
        "id": event_id,
        "status": status,
        "summary": ENTRY.title,
        "start": {"dateTime": ENTRY.interval.start.isoformat()},
        "end": {"dateTime": ENTRY.interval.end.isoformat()},
        "extendedProperties": {"private": {KEY_PROPERTY: key, WEEK_PROPERTY: WEEK.render()}},
    }


def test_first_use_creates_the_life_os_calendar_once(
    stub_server: StubServer, tmp_path: Path
) -> None:
    client, store = writer(stub_server, tmp_path)
    stub_server.replies += [reply({"id": CAL_ID}), reply({"items": []}), reply({"items": []})]
    assert client.list_entries(WEEK.render(), START, END) == ()
    client.list_entries(WEEK.render(), START, END)
    assert store.calendar_id() == CAL_ID
    created = stub_server.received[0]
    assert (created.method, created.path) == ("POST", "/v1/calendars")
    assert json.loads(created.body)["summary"] == "Life OS"
    assert sum(r.path == "/v1/calendars" for r in stub_server.received) == 1


def test_list_filters_by_week_marker_and_skips_cancelled(
    stub_server: StubServer, tmp_path: Path
) -> None:
    client, store = writer(stub_server, tmp_path)
    store.save_calendar_id(CAL_ID)
    stub_server.replies += [
        reply({"items": [event("e1")], "nextPageToken": "p2"}),
        reply({"items": [event("e2", status="cancelled"), event("e3", key="manipulada")]}),
    ]
    entries = client.list_entries(WEEK.render(), START, END)
    assert [(e.event_id, e.key) for e in entries] == [("e1", ENTRY.key), ("e3", "")]
    first = urlsplit(stub_server.received[0].path)
    assert unquote(first.path) == f"/v1/calendars/{CAL_ID}/events"
    query = parse_qs(first.query)
    assert query["privateExtendedProperty"] == [f"{WEEK_PROPERTY}={WEEK.render()}"]
    assert parse_qs(urlsplit(stub_server.received[1].path).query)["pageToken"] == ["p2"]


def test_deleted_life_os_calendar_is_forgotten(stub_server: StubServer, tmp_path: Path) -> None:
    client, store = writer(stub_server, tmp_path)
    store.save_calendar_id(CAL_ID)
    stub_server.replies.append(reply({}, 404))
    assert client.list_entries(WEEK.render(), START, END) == ()
    assert store.calendar_id() is None


def test_create_update_and_delete_send_the_private_markers(
    stub_server: StubServer, tmp_path: Path
) -> None:
    client, store = writer(stub_server, tmp_path)
    store.save_calendar_id(CAL_ID)
    stub_server.replies += [reply(event("e1")), reply(event("e1")), reply({}, 204)]
    client.create(WEEK.render(), ENTRY)
    client.update("e1", WEEK.render(), ENTRY)
    client.delete("e1")
    methods = [r.method for r in stub_server.received]
    assert methods == ["POST", "PUT", "DELETE"]
    body = json.loads(stub_server.received[0].body)
    assert body["extendedProperties"]["private"] == {
        KEY_PROPERTY: ENTRY.key,
        WEEK_PROPERTY: WEEK.render(),
    }
    assert body["summary"] == ENTRY.title


def test_deleting_an_event_that_no_longer_exists_is_done(
    stub_server: StubServer, tmp_path: Path
) -> None:
    client, store = writer(stub_server, tmp_path)
    store.save_calendar_id(CAL_ID)
    stub_server.replies += [reply({}, 410), reply({}, 404)]
    client.delete("e1")
    client.delete("e2")


def test_suspicious_event_ids_never_reach_a_url(stub_server: StubServer, tmp_path: Path) -> None:
    client, store = writer(stub_server, tmp_path)
    store.save_calendar_id(CAL_ID)
    with pytest.raises(CalendarError, match="id de evento"):
        client.delete("../../calendars/primary")
    assert stub_server.received == []


def test_rejected_write_is_an_explicit_error(stub_server: StubServer, tmp_path: Path) -> None:
    client, store = writer(stub_server, tmp_path)
    store.save_calendar_id(CAL_ID)
    stub_server.replies.append(reply({"error": {"errors": [{"reason": "rateLimitExceeded"}]}}, 429))
    with pytest.raises(CalendarError, match="rateLimitExceeded"):
        client.create(WEEK.render(), ENTRY)
