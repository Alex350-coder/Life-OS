"""Google Calendar API v3 (ADR-0017): disponibilidad y calendario propio de Life OS.

- Lectura (`GoogleBusyReader`): `freeBusy` del calendario principal. Solo llegan intervalos
  ocupados, nunca títulos ni descripciones.
- Escritura (`GoogleCalendarWriter`): un calendario secundario "Life OS" creado por la propia
  aplicación (`calendar.app.created`). Los eventos llevan propiedades privadas `lifeosClave` y
  `lifeosSemana`; solo se listan y tocan los de esa semana, en ese calendario.

Un 401 renueva el access token una vez y repite. Las escrituras no se reintentan solas: la
siguiente publicación es idempotente y corrige lo que haya quedado a medias.
"""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Protocol
from urllib.parse import quote, urlencode, urlsplit

from lifeos.adapters.google_http import LOOPBACK, GoogleHttp, Response, check_base_url
from lifeos.adapters.google_tokens import TokenStore
from lifeos.adapters.secrets import Secret
from lifeos.domain.calendar_sync import CalendarEntry, PublishedEntry
from lifeos.domain.capacity import Interval
from lifeos.domain.errors import ValidationError
from lifeos.domain.external import ExternalBusy, busy_from
from lifeos.ports.calendar import CalendarAuthError, CalendarError

API_BASE = "https://www.googleapis.com/calendar/v3"
PRIMARY = "primary"
SOURCE = "google:primary"
CALENDAR_NAME = "Life OS"
KEY_PROPERTY = "lifeosClave"
WEEK_PROPERTY = "lifeosSemana"
MAX_BUSY = 500
MAX_PAGES = 20
PAGE_SIZE = 250
_EVENT_ID_RE = re.compile(r"[a-z0-9_-]{1,1024}", re.ASCII)
_KEY_RE = re.compile(r"[0-9a-f]{32}", re.ASCII)
_WEEK_RE = re.compile(r"[0-9]{4}-W[0-9]{2}", re.ASCII)
_DESCRIPTION = "Bloque de trabajo aprobado en Life OS. Se actualiza al republicar el plan."


def _rfc3339(moment: datetime) -> str:
    if moment.utcoffset() is None:
        raise CalendarError("las fechas para Google requieren zona horaria")
    return moment.isoformat()


def _moment(raw: object) -> datetime:
    if not isinstance(raw, str):
        raise ValueError("fecha ausente")
    moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if moment.utcoffset() is None:
        raise ValueError("fecha sin zona")
    return moment


class TokenSource(Protocol):
    """Access token de un permiso (`AccessTokens` en producción)."""

    def get(self) -> Secret: ...

    def invalidate(self) -> None: ...


class _Api:
    """Llamadas autenticadas con un permiso concreto."""

    def __init__(self, tokens: TokenSource, base_url: str, http: GoogleHttp | None) -> None:
        self._tokens = tokens
        self._base = check_base_url(base_url)
        self._http = http or GoogleHttp(loopback=urlsplit(self._base).hostname == LOOPBACK)

    def call(self, method: str, path: str, body: dict[str, object] | None = None) -> Response:
        url = f"{self._base}{path}"
        response = self._http.request(method, url, body=body, bearer=self._tokens.get())
        if response.status == 401:  # access token caducado o revocado: renovar una vez
            self._tokens.invalidate()
            response = self._http.request(method, url, body=body, bearer=self._tokens.get())
        if response.status == 403 and response.error_code() in {
            "insufficientPermissions",
            "ACCESS_TOKEN_SCOPE_INSUFFICIENT",
            "PERMISSION_DENIED",
        }:
            raise CalendarAuthError(f"Google rechazó el permiso: {response.describe()}")
        return response


class GoogleBusyReader:
    """`BusyReader` con el token de lectura (`calendar.freebusy`)."""

    def __init__(
        self, tokens: TokenSource, *, base_url: str = API_BASE, http: GoogleHttp | None = None
    ) -> None:
        self._api = _Api(tokens, base_url, http)

    def busy(self, start: datetime, end: datetime) -> tuple[ExternalBusy, ...]:
        body: dict[str, object] = {
            "timeMin": _rfc3339(start),
            "timeMax": _rfc3339(end),
            "items": [{"id": PRIMARY}],
        }
        response = self._api.call("POST", "/freeBusy", body)
        if not response.ok:
            raise CalendarError(f"no se pudo leer la disponibilidad: {response.describe()}")
        calendars = response.data.get("calendars")
        primary = calendars.get(PRIMARY) if isinstance(calendars, dict) else None
        if not isinstance(primary, dict):
            raise CalendarError("Google no devolvió la disponibilidad del calendario principal")
        if primary.get("errors"):
            raise CalendarError("Google no pudo calcular la disponibilidad del calendario")
        raw = primary.get("busy", [])
        if not isinstance(raw, list) or len(raw) > MAX_BUSY:
            raise CalendarError("Google devolvió una lista de ocupación no válida")
        return tuple(item for item in map(_busy, raw) if item is not None)


def _busy(raw: object) -> ExternalBusy | None:
    if not isinstance(raw, dict):
        raise CalendarError("Google devolvió un intervalo ocupado no válido")
    try:
        start, end = _moment(raw.get("start")), _moment(raw.get("end"))
        if end <= start:
            return None  # intervalo vacío: no ocupa nada
        return busy_from(Interval(start, end), SOURCE)
    except (ValueError, ValidationError):
        raise CalendarError("Google devolvió un intervalo ocupado no válido") from None


class GoogleCalendarWriter:
    """`CalendarWriter` con el token de escritura (`calendar.app.created`)."""

    def __init__(
        self,
        tokens: TokenSource,
        store: TokenStore,
        *,
        time_zone: str,
        base_url: str = API_BASE,
        http: GoogleHttp | None = None,
    ) -> None:
        self._api = _Api(tokens, base_url, http)
        self._store = store
        self._time_zone = time_zone

    # --- Calendario propio ----------------------------------------------------------------------

    def _create_calendar(self) -> str:
        response = self._api.call(
            "POST", "/calendars", {"summary": CALENDAR_NAME, "timeZone": self._time_zone}
        )
        calendar_id = response.data.get("id") if response.ok else None
        if not isinstance(calendar_id, str) or not calendar_id or len(calendar_id) > 1024:
            raise CalendarError(f"no se pudo crear el calendario Life OS: {response.describe()}")
        self._store.save_calendar_id(calendar_id)
        return calendar_id

    def _calendar(self) -> str:
        return self._store.calendar_id() or self._create_calendar()

    def _events_path(self, calendar_id: str, event_id: str | None = None) -> str:
        path = f"/calendars/{quote(calendar_id, safe='')}/events"
        if event_id is None:
            return path
        if not _EVENT_ID_RE.fullmatch(event_id):
            raise CalendarError("Google devolvió un id de evento no válido")
        return f"{path}/{event_id}"

    # --- Lectura de lo publicado ---------------------------------------------------------------

    def list_entries(
        self, week_label: str, start: datetime, end: datetime
    ) -> tuple[PublishedEntry, ...]:
        if not _WEEK_RE.fullmatch(week_label):
            raise CalendarError("semana no válida")
        calendar_id = self._calendar()
        entries: list[PublishedEntry] = []
        token: str | None = None
        for _ in range(MAX_PAGES):
            query = {
                "privateExtendedProperty": f"{WEEK_PROPERTY}={week_label}",
                "timeMin": _rfc3339(start),
                "timeMax": _rfc3339(end),
                "singleEvents": "true",
                "maxResults": str(PAGE_SIZE),
                **({"pageToken": token} if token else {}),
            }
            path = f"{self._events_path(calendar_id)}?{urlencode(query)}"
            response = self._api.call("GET", path)
            if response.status == 404:
                # El propietario borró el calendario: se olvida y se crea otro al publicar.
                self._store.forget_calendar()
                return ()
            if not response.ok:
                raise CalendarError(f"no se pudieron leer los eventos: {response.describe()}")
            entries += _entries(response.data.get("items"))
            next_token = response.data.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return tuple(entries)
            token = next_token
        raise CalendarError("demasiados eventos de Life OS en una semana")

    # --- Escritura ------------------------------------------------------------------------------

    def _body(self, week_label: str, entry: CalendarEntry) -> dict[str, object]:
        return {
            "summary": entry.title,
            "description": _DESCRIPTION,
            "start": {"dateTime": _rfc3339(entry.interval.start)},
            "end": {"dateTime": _rfc3339(entry.interval.end)},
            "extendedProperties": {"private": {KEY_PROPERTY: entry.key, WEEK_PROPERTY: week_label}},
        }

    def _write(self, method: str, path_of: Callable[[str], str], body: dict[str, object]) -> None:
        response = self._api.call(method, path_of(self._calendar()), body)
        if not response.ok:
            raise CalendarError(f"Google rechazó el evento: {response.describe()}")

    def create(self, week_label: str, entry: CalendarEntry) -> None:
        self._write("POST", self._events_path, self._body(week_label, entry))

    def update(self, event_id: str, week_label: str, entry: CalendarEntry) -> None:
        self._write(
            "PUT", lambda cal: self._events_path(cal, event_id), self._body(week_label, entry)
        )

    def delete(self, event_id: str) -> None:
        response = self._api.call("DELETE", self._events_path(self._calendar(), event_id))
        if not response.ok and response.status not in (404, 410):  # ya no existe: hecho
            raise CalendarError(f"Google no borró el evento: {response.describe()}")


def _entries(raw: object) -> list[PublishedEntry]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise CalendarError("Google devolvió una lista de eventos no válida")
    found = []
    for item in raw:
        entry = _entry(item)
        if entry is not None:
            found.append(entry)
    return found


def _entry(raw: object) -> PublishedEntry | None:
    if not isinstance(raw, dict) or raw.get("status") == "cancelled":
        return None
    event_id = raw.get("id")
    if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
        raise CalendarError("Google devolvió un id de evento no válido")
    props = raw.get("extendedProperties")
    private = props.get("private") if isinstance(props, dict) else None
    key = private.get(KEY_PROPERTY) if isinstance(private, dict) else None
    title = raw.get("summary")
    try:
        start = _moment(_field(raw.get("start")))
        end = _moment(_field(raw.get("end")))
        interval = Interval(start, end)
    except (ValueError, ValidationError):
        raise CalendarError("Google devolvió un evento con fechas no válidas") from None
    return PublishedEntry(
        event_id=event_id,
        # Una clave ausente o alterada no coincide con ninguna deseada: el evento se reemplaza.
        key=key if isinstance(key, str) and _KEY_RE.fullmatch(key) else "",
        title=title if isinstance(title, str) else "",
        interval=interval,
    )


def _field(raw: object) -> object:
    return raw.get("dateTime") if isinstance(raw, dict) else None
