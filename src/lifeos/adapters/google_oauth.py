"""OAuth 2.0 de Google para una aplicación de escritorio (ADR-0017, SEC-13).

- Flujo con redirección a 127.0.0.1 y PKCE (S256); `state` aleatorio comparado en tiempo
  constante. El servidor local solo escucha en 127.0.0.1 y atiende una autorización.
- Un permiso por autorización: lectura (`calendar.freebusy`) o escritura
  (`calendar.app.created`). Si Google concede algo distinto de lo pedido, el token se revoca y
  no se guarda.
- El access token solo vive en memoria. Un refresh rechazado con `invalid_grant` significa que
  el acceso se revocó: el token local se borra y hay que volver a conectar.
- Revocar en Google revoca toda la autorización de este cliente (lectura y escritura juntas).
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from lifeos.adapters.google_http import LOOPBACK, GoogleHttp, check_base_url
from lifeos.adapters.google_tokens import Grant, StoredToken, TokenStore
from lifeos.adapters.secrets import Secret
from lifeos.ports.calendar import CalendarAuthError, CalendarError

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105  # nosec B105 - URL, no un token
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
LOGIN_TIMEOUT_S = 300
EXPIRY_MARGIN_S = 60
MAX_EXPIRES_S = 24 * 3600
_CLIENT_ID_SUFFIX = ".apps.googleusercontent.com"
_DONE_PAGE = (
    "<!doctype html><meta charset='utf-8'><title>Life OS</title>"
    "<p>Life OS: respuesta recibida. Ya puedes cerrar esta pestaña.</p>"
).encode()


@dataclass(frozen=True, slots=True)
class OAuthEndpoints:
    auth: str = AUTH_URL
    token: str = TOKEN_URL
    revoke: str = REVOKE_URL

    def __post_init__(self) -> None:
        for url in (self.auth, self.token, self.revoke):
            check_base_url(url)


@dataclass(frozen=True, slots=True)
class AccessToken:
    value: Secret
    expires_at: float  # reloj monotónico


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _expires_in(data: dict[str, object]) -> int:
    raw = data.get("expires_in")
    if isinstance(raw, bool) or not isinstance(raw, int) or not 0 < raw <= MAX_EXPIRES_S:
        raise CalendarError("Google devolvió una caducidad de token no válida")
    return raw


def _secret_field(data: dict[str, object], name: str) -> Secret:
    raw = data.get(name)
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 4096:
        raise CalendarError(f"Google no devolvió {name}")
    return Secret(raw)


class CalendarRevokedError(CalendarAuthError):
    """Google rechazó el refresh token (`invalid_grant`): el acceso se revocó o caducó."""

    def __init__(self, grant: Grant) -> None:
        super().__init__(
            f"el acceso de {grant} a Google Calendar fue revocado o caducó; vuelve a autorizar con "
            f"'lifeos-calendario conectar --{grant}'"
        )
        self.grant = grant


class GoogleOAuth:
    def __init__(
        self,
        client_id: str,
        client_secret: Secret | None,
        *,
        endpoints: OAuthEndpoints | None = None,
        http: GoogleHttp | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if not client_id.endswith(_CLIENT_ID_SUFFIX) or len(client_id) > 256:
            raise ValueError(f"GOOGLE_CLIENT_ID debe terminar en {_CLIENT_ID_SUFFIX}")
        self._client_id = client_id
        self._client_secret = client_secret
        self._endpoints = endpoints or OAuthEndpoints()
        loopback = urlsplit(self._endpoints.token).hostname == LOOPBACK
        self._http = http or GoogleHttp(loopback=loopback)
        self._monotonic = monotonic

    def __repr__(self) -> str:
        return "GoogleOAuth(client_id=***, client_secret=***)"

    def _client_form(self) -> dict[str, str]:
        form = {"client_id": self._client_id}
        if self._client_secret is not None:
            form["client_secret"] = self._client_secret.reveal()
        return form

    # --- Autorización ---------------------------------------------------------------------------

    def authorization_url(self, grant: Grant, redirect_uri: str, state: str, verifier: str) -> str:
        query = {
            "client_id": self._client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": grant.scope,  # un solo permiso por autorización (SEC-13)
            "state": state,
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }
        return f"{self._endpoints.auth}?{urlencode(query)}"

    def exchange(
        self, grant: Grant, code: str, redirect_uri: str, verifier: str, *, now: datetime
    ) -> StoredToken:
        response = self._http.request(
            "POST",
            self._endpoints.token,
            form={
                **self._client_form(),
                "code": code,
                "code_verifier": verifier,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
        )
        if not response.ok:
            raise CalendarAuthError(f"no se pudo completar la autorización: {response.describe()}")
        refresh = _secret_field(response.data, "refresh_token")
        scope = response.data.get("scope")
        granted = set(scope.split()) if isinstance(scope, str) else set()
        if granted != {grant.scope}:
            # Más (o menos) de lo pedido: no se guarda y se revoca enseguida.
            self._revoke_quietly(refresh)
            raise CalendarAuthError(
                "Google concedió permisos distintos de los pedidos "
                f"({len(granted)} scope(s)); no se guardó nada. Pide solo {grant.scope}"
            )
        return StoredToken(grant, refresh, grant.scope, now)

    # --- Uso ------------------------------------------------------------------------------------

    def refresh(self, token: StoredToken) -> AccessToken:
        response = self._http.request(
            "POST",
            self._endpoints.token,
            form={
                **self._client_form(),
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token.reveal(),
            },
        )
        if response.error_code() == "invalid_grant":
            raise CalendarRevokedError(token.grant)
        if not response.ok:
            raise CalendarError(f"no se pudo renovar el acceso: {response.describe()}")
        scope = response.data.get("scope")
        if isinstance(scope, str) and set(scope.split()) != {token.grant.scope}:
            raise CalendarAuthError("el token de Google trae permisos distintos de los pedidos")
        value = _secret_field(response.data, "access_token")
        return AccessToken(value, self._monotonic() + _expires_in(response.data))

    def revoke(self, token: StoredToken) -> bool:
        """True si Google lo revocó ahora; False si ya no era válido (400)."""
        response = self._http.request(
            "POST", self._endpoints.revoke, form={"token": token.refresh_token.reveal()}
        )
        if response.ok:
            return True
        if response.status == 400:
            return False
        raise CalendarError(f"Google no confirmó la revocación: {response.describe()}")

    def _revoke_quietly(self, refresh: Secret) -> None:
        try:
            self._http.request("POST", self._endpoints.revoke, form={"token": refresh.reveal()})
        except CalendarError:
            return  # el token no se guarda de todos modos; el error principal se informa arriba


class AccessTokens:
    """Access token en memoria para un permiso; se renueva antes de caducar."""

    def __init__(self, oauth: GoogleOAuth, store: TokenStore, grant: Grant) -> None:
        self._oauth = oauth
        self._store = store
        self._grant = grant
        self._current: AccessToken | None = None
        self._lock = threading.Lock()

    def get(self, monotonic: Callable[[], float] = time.monotonic) -> Secret:
        with self._lock:
            current = self._current
            if current is not None and current.expires_at - EXPIRY_MARGIN_S > monotonic():
                return current.value
            stored = self._store.load(self._grant)
            if stored is None:
                raise CalendarAuthError(
                    f"Google Calendar no está autorizado ({self._grant}): usa "
                    f"'lifeos-calendario conectar --{self._grant}'"
                )
            try:
                self._current = self._oauth.refresh(stored)
            except CalendarRevokedError:
                self._store.remove(self._grant)  # inútil: no se vuelve a intentar con él
                raise
            return self._current.value

    def invalidate(self) -> None:
        with self._lock:
            self._current = None


# --- Flujo interactivo con redirección a 127.0.0.1 ----------------------------------------------


@dataclass
class _Callback:
    state: str
    code: str | None = None
    error: str | None = None
    done: threading.Event = field(default_factory=threading.Event)


def _handler(callback: _Callback) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return  # la URL lleva el código de autorización: no se registra

        def do_GET(self) -> None:
            query = parse_qs(urlsplit(self.path).query)
            state = query.get("state", [""])[0]
            if urlsplit(self.path).path != "/" or not hmac.compare_digest(
                state.encode(), callback.state.encode()
            ):
                self.send_error(404)  # p. ej., /favicon.ico o un state ajeno: se ignora
                return
            callback.code = query.get("code", [None])[0]
            callback.error = query.get("error", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(_DONE_PAGE)))
            self.end_headers()
            self.wfile.write(_DONE_PAGE)
            callback.done.set()

    return Handler


def run_loopback_flow(
    oauth: GoogleOAuth,
    grant: Grant,
    open_browser: Callable[[str], object],
    *,
    now: Callable[[], datetime],
    timeout_s: float = LOGIN_TIMEOUT_S,
) -> StoredToken:
    """Abre el navegador, espera la redirección a 127.0.0.1 y canjea el código."""
    callback = _Callback(state=secrets.token_urlsafe(32))
    verifier = secrets.token_urlsafe(64)  # 86 caracteres: dentro de 43–128 (RFC 7636)
    server = HTTPServer((LOOPBACK, 0), _handler(callback))
    redirect_uri = f"http://{LOOPBACK}:{server.server_address[1]}/"
    thread = threading.Thread(target=server.serve_forever, args=(0.1,), daemon=True)
    thread.start()
    try:
        open_browser(oauth.authorization_url(grant, redirect_uri, callback.state, verifier))
        if not callback.done.wait(timeout_s):
            raise CalendarAuthError("no llegó la respuesta de Google a tiempo; vuelve a intentarlo")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if callback.error is not None or not callback.code:
        raise CalendarAuthError("la autorización se canceló o se rechazó en Google")
    return oauth.exchange(grant, callback.code, redirect_uri, verifier, now=now())
