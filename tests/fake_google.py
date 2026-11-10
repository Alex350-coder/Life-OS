"""Google falso con estado para pruebas de punta a punta (nunca se usa fuera de tests).

Imita lo que importa a SEC-13: cada token lleva solo el scope autorizado, freebusy exige
`calendar.freebusy`, el calendario propio exige `calendar.app.created`, y revocar invalida todos
los tokens del cliente (como Google). Datos inventados.
"""

import itertools
import json
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

READ = "https://www.googleapis.com/auth/calendar.freebusy"
WRITE = "https://www.googleapis.com/auth/calendar.app.created"


@dataclass
class FakeGoogle:
    base: str = ""
    busy: list[dict[str, str]] = field(default_factory=list)
    codes: dict[str, str] = field(default_factory=dict)  # código → scope
    refresh: dict[str, str] = field(default_factory=dict)  # refresh token → scope
    access: dict[str, str] = field(default_factory=dict)  # access token → scope
    revoked: bool = False
    revocations: int = 0
    calendars: dict[str, dict[str, dict[str, object]]] = field(default_factory=dict)
    _ids: Iterator[int] = field(default_factory=lambda: itertools.count(1))

    @property
    def oauth_base(self) -> str:
        return f"{self.base}/oauth"

    @property
    def api_base(self) -> str:
        return f"{self.base}/api"

    def authorize(self, auth_url: str) -> str:
        """Lo que hace el navegador: Google aprueba el scope pedido y redirige con un código."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(auth_url).query).items()}
        code = f"code-{next(self._ids)}"
        self.codes[code] = query["scope"]
        return f"{query['redirect_uri']}?state={query['state']}&code={code}"

    def events(self) -> list[dict[str, object]]:
        return [e for cal in self.calendars.values() for e in cal.values()]


def _token(server: FakeGoogle, form: dict[str, str]) -> tuple[int, dict[str, object]]:
    if form.get("grant_type") == "authorization_code":
        scope = server.codes.pop(form.get("code", ""), None)
        if scope is None:
            return 400, {"error": "invalid_grant"}
        refresh = f"1//r{next(server._ids)}"
        server.refresh[refresh] = scope
        server.revoked = False
        return 200, {"access_token": "x", "refresh_token": refresh, "scope": scope}
    scope = server.refresh.get(form.get("refresh_token", ""))
    if scope is None or server.revoked:
        return 400, {"error": "invalid_grant"}
    access = f"ya29.a{next(server._ids)}"
    server.access[access] = scope
    return 200, {"access_token": access, "expires_in": 3599, "scope": scope}


def _handler(server: FakeGoogle) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def _send(self, status: int, data: object) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> bytes:
            length = int(self.headers.get("Content-Length") or 0)
            return self.rfile.read(length) if length else b""

        def _scope(self) -> str | None:
            auth = self.headers.get("Authorization", "")
            if server.revoked:
                return None
            return server.access.get(auth.removeprefix("Bearer "))

        def _route(self) -> None:
            parts = urlsplit(self.path)
            path = parts.path
            if path == "/oauth/token":
                form = {k: v[0] for k, v in parse_qs(self._body().decode()).items()}
                self._send(*_token(server, form))
                return
            if path == "/oauth/revoke":
                form = {k: v[0] for k, v in parse_qs(self._body().decode()).items()}
                known = form.get("token") in server.refresh and not server.revoked
                if known:
                    server.revoked = True  # Google revoca toda la autorización del cliente
                    server.revocations += 1
                self._send(200 if known else 400, {} if known else {"error": "invalid_token"})
                return
            scope = self._scope()
            if scope is None:
                self._send(401, {"error": {"status": "UNAUTHENTICATED"}})
                return
            self._api(path, parts.query, scope)

        def _forbidden(self) -> None:
            self._send(403, {"error": {"errors": [{"reason": "insufficientPermissions"}]}})

        def _api(self, path: str, query: str, scope: str) -> None:
            if path == "/api/freeBusy":
                if scope != READ:
                    self._forbidden()
                    return
                self._body()
                self._send(200, {"calendars": {"primary": {"busy": server.busy}}})
                return
            if scope != WRITE:
                self._forbidden()
                return
            if path == "/api/calendars" and self.command == "POST":
                self._body()
                cal_id = f"cal{next(server._ids)}@group.calendar.google.com"
                server.calendars[cal_id] = {}
                self._send(200, {"id": cal_id})
                return
            self._events(path, query)

        def _events(self, path: str, query: str) -> None:
            pieces = path.split("/")  # ['', 'api', 'calendars', id, 'events', (event)]
            cal = server.calendars.get(unquote(pieces[3])) if len(pieces) > 4 else None
            if cal is None:
                self._send(404, {"error": {"status": "NOT_FOUND"}})
                return
            event_id = pieces[5] if len(pieces) > 5 else None
            if self.command == "GET":
                wanted = parse_qs(query)["privateExtendedProperty"][0].split("=", 1)
                items = [
                    e
                    for e in cal.values()
                    if e["extendedProperties"]["private"].get(wanted[0]) == wanted[1]  # type: ignore[index]
                ]
                self._send(200, {"items": items})
            elif self.command == "POST":
                new_id = f"ev{next(server._ids)}"
                cal[new_id] = {**json.loads(self._body()), "id": new_id, "status": "confirmed"}
                self._send(200, cal[new_id])
            elif self.command == "PUT" and event_id in cal:
                cal[event_id] = {**json.loads(self._body()), "id": event_id}
                self._send(200, cal[event_id])
            elif self.command == "DELETE" and event_id in cal:
                del cal[event_id]
                self._send(204, {})
            else:
                self._send(404, {"error": {"status": "NOT_FOUND"}})

        do_GET = _route
        do_POST = _route
        do_PUT = _route
        do_DELETE = _route

    return Handler


@pytest.fixture
def fake_google() -> Iterator[FakeGoogle]:
    state = FakeGoogle()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    state.base = f"http://127.0.0.1:{server.server_address[1]}"
    thread = threading.Thread(target=server.serve_forever, args=(0.02,), daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
