"""OAuth de Google contra un servidor local (ADR-0017, SEC-08, SEC-13).

Credenciales inventadas. Se comprueba: un solo scope por autorización, PKCE S256, rechazo y
revocación de un token con permisos de más, revocación remota detectada (`invalid_grant`) y que
ningún token aparece en errores ni en `repr`.
"""

import base64
import contextlib
import hashlib
import json
import threading
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from lifeos.adapters.google_http import GoogleHttp
from lifeos.adapters.google_oauth import (
    AccessTokens,
    CalendarRevokedError,
    GoogleOAuth,
    OAuthEndpoints,
    run_loopback_flow,
)
from lifeos.adapters.google_tokens import READ_SCOPE, WRITE_SCOPE, Grant, StoredToken, TokenStore
from lifeos.adapters.secrets import Secret
from lifeos.ports.calendar import CalendarAuthError, CalendarError
from tests.stub_http import Reply, StubServer

pytestmark = [pytest.mark.integration, pytest.mark.security]
CLIENT_ID = "123-prueba.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-secreto-de-prueba"  # noqa: S105 - inventado
REFRESH = "1//refresh-de-prueba"
ACCESS = "ya29.access-de-prueba"
NOW = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)


def reply(data: dict[str, object], status: int = 200) -> Reply:
    return Reply(status=status, body=json.dumps(data).encode())


def oauth(stub: StubServer) -> GoogleOAuth:
    base = stub.base_url.removesuffix("/v1")
    return GoogleOAuth(
        CLIENT_ID,
        Secret(CLIENT_SECRET),
        endpoints=OAuthEndpoints(f"{base}/auth", f"{base}/token", f"{base}/revoke"),
        http=GoogleHttp(loopback=True),
    )


def stored(grant: Grant = Grant.LECTURA) -> StoredToken:
    return StoredToken(grant, Secret(REFRESH), grant.scope, NOW)


def form(body: bytes) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(body.decode()).items()}


# --- Autorización ---------------------------------------------------------------------------------


def test_authorization_url_asks_for_one_scope_with_pkce() -> None:
    client = GoogleOAuth(CLIENT_ID, None)
    url = client.authorization_url(Grant.LECTURA, "http://127.0.0.1:5000/", "st", "v" * 50)
    query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert query["scope"] == READ_SCOPE  # nunca lectura y escritura juntas
    assert query["code_challenge_method"] == "S256"
    expected = base64.urlsafe_b64encode(hashlib.sha256(b"v" * 50).digest()).rstrip(b"=")
    assert query["code_challenge"] == expected.decode()
    assert "include_granted_scopes" not in query


def test_exchange_stores_only_the_requested_scope(stub_server: StubServer) -> None:
    stub_server.replies.append(
        reply({"access_token": ACCESS, "refresh_token": REFRESH, "scope": WRITE_SCOPE})
    )
    token = oauth(stub_server).exchange(
        Grant.ESCRITURA, "code", "http://127.0.0.1:1/", "v", now=NOW
    )
    assert (token.grant, token.scope) == (Grant.ESCRITURA, WRITE_SCOPE)
    sent = form(stub_server.received[0].body)
    assert sent["grant_type"] == "authorization_code" and sent["code_verifier"] == "v"


@pytest.mark.parametrize(
    "scope",
    [
        f"{READ_SCOPE} https://www.googleapis.com/auth/calendar",  # más de lo pedido
        "https://www.googleapis.com/auth/calendar",  # otro permiso más amplio
        "",  # sin scope
    ],
)
def test_token_with_other_scopes_is_revoked_and_never_stored(
    stub_server: StubServer, scope: str
) -> None:
    stub_server.replies += [
        reply({"access_token": ACCESS, "refresh_token": REFRESH, "scope": scope}),
        reply({}),  # revocación
    ]
    with pytest.raises(CalendarAuthError, match="permisos distintos"):
        oauth(stub_server).exchange(Grant.LECTURA, "code", "http://127.0.0.1:1/", "v", now=NOW)
    assert stub_server.received[-1].path == "/revoke"
    assert form(stub_server.received[-1].body) == {"token": REFRESH}


def test_failed_exchange_never_reveals_secrets(stub_server: StubServer) -> None:
    stub_server.replies.append(reply({"error": "invalid_client"}, 401))
    with pytest.raises(CalendarAuthError) as error:
        oauth(stub_server).exchange(Grant.LECTURA, "code", "http://127.0.0.1:1/", "v", now=NOW)
    message = str(error.value)
    assert "invalid_client" in message
    assert CLIENT_SECRET not in message and CLIENT_ID not in message


def test_repr_hides_client_credentials() -> None:
    assert CLIENT_ID not in repr(GoogleOAuth(CLIENT_ID, Secret(CLIENT_SECRET)))
    assert REFRESH not in repr(stored())


def test_client_id_must_be_a_google_desktop_client() -> None:
    with pytest.raises(ValueError, match=r"apps\.googleusercontent\.com"):
        GoogleOAuth("cualquier-cosa", None)


def test_endpoints_must_be_google_or_loopback() -> None:
    with pytest.raises(ValueError):
        OAuthEndpoints(token="https://evil.example.com/token")  # noqa: S106 - URL
    with pytest.raises(ValueError):
        OAuthEndpoints(token="http://oauth2.googleapis.com/token")  # noqa: S106 - sin TLS


# --- Refresh y revocación (SEC-13) ----------------------------------------------------------------


def test_refresh_returns_an_access_token_in_memory(stub_server: StubServer) -> None:
    stub_server.replies.append(
        reply({"access_token": ACCESS, "expires_in": 3599, "scope": READ_SCOPE})
    )
    access = oauth(stub_server).refresh(stored())
    assert access.value.reveal() == ACCESS
    assert ACCESS not in repr(access)


def test_refresh_after_remote_revocation_removes_the_local_token(
    stub_server: StubServer, tmp_path: Path
) -> None:
    store = TokenStore(tmp_path)
    store.save(stored())
    stub_server.replies.append(reply({"error": "invalid_grant"}, 400))
    tokens = AccessTokens(oauth(stub_server), store, Grant.LECTURA)
    with pytest.raises(CalendarRevokedError, match="conectar --lectura"):
        tokens.get()
    assert not store.has(Grant.LECTURA)


def test_access_token_is_reused_until_it_expires(stub_server: StubServer, tmp_path: Path) -> None:
    store = TokenStore(tmp_path)
    store.save(stored())
    stub_server.replies += [
        reply({"access_token": ACCESS, "expires_in": 3600}),
        reply({"access_token": "ya29.otro", "expires_in": 3600}),
    ]
    tokens = AccessTokens(oauth(stub_server), store, Grant.LECTURA)
    assert tokens.get().reveal() == ACCESS
    assert tokens.get().reveal() == ACCESS
    assert len(stub_server.received) == 1
    tokens.invalidate()
    assert tokens.get().reveal() == "ya29.otro"


def test_without_a_stored_token_the_error_says_how_to_connect(tmp_path: Path) -> None:
    tokens = AccessTokens(GoogleOAuth(CLIENT_ID, None), TokenStore(tmp_path), Grant.ESCRITURA)
    with pytest.raises(CalendarAuthError, match="conectar --escritura"):
        tokens.get()


def test_revoke_confirms_or_reports_an_already_invalid_token(stub_server: StubServer) -> None:
    stub_server.replies += [reply({}), reply({"error": "invalid_token"}, 400)]
    client = oauth(stub_server)
    assert client.revoke(stored()) is True
    assert client.revoke(stored()) is False
    assert all(r.path == "/revoke" for r in stub_server.received)


def test_revoke_failure_is_explicit(stub_server: StubServer) -> None:
    stub_server.replies.append(reply({}, 503))
    with pytest.raises(CalendarError, match="503"):
        oauth(stub_server).revoke(stored())


# --- Flujo con redirección a 127.0.0.1 -----------------------------------------------------------


def _visit(url: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        response.read()


def test_loopback_flow_ignores_a_wrong_state_and_accepts_the_right_one(
    stub_server: StubServer,
) -> None:
    stub_server.replies.append(
        reply({"access_token": ACCESS, "refresh_token": REFRESH, "scope": READ_SCOPE})
    )

    def browser(url: str) -> None:
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        redirect = query["redirect_uri"]
        assert urlsplit(redirect).hostname == "127.0.0.1"

        def answer() -> None:
            with contextlib.suppress(OSError):  # 404 esperado: un state ajeno se ignora
                _visit(f"{redirect}?state=ajeno&code=robado")
            _visit(f"{redirect}?state={query['state']}&code=bueno")

        threading.Thread(target=answer, daemon=True).start()

    token = run_loopback_flow(oauth(stub_server), Grant.LECTURA, browser, now=lambda: NOW)
    assert token.scope == READ_SCOPE
    assert form(stub_server.received[0].body)["code"] == "bueno"


def test_loopback_flow_reports_a_denied_consent(stub_server: StubServer) -> None:
    def browser(url: str) -> None:
        query = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        target = f"{query['redirect_uri']}?state={query['state']}&error=access_denied"
        threading.Thread(target=_visit, args=(target,), daemon=True).start()

    with pytest.raises(CalendarAuthError, match="cancel"):
        run_loopback_flow(oauth(stub_server), Grant.LECTURA, browser, now=lambda: NOW)
    assert stub_server.received == []  # no se canjeó nada


def test_loopback_flow_times_out(stub_server: StubServer) -> None:
    with pytest.raises(CalendarAuthError, match="a tiempo"):
        run_loopback_flow(
            oauth(stub_server), Grant.LECTURA, lambda url: None, now=lambda: NOW, timeout_s=0.2
        )


# --- Almacén de tokens ----------------------------------------------------------------------------


def test_token_store_round_trip_and_removal(tmp_path: Path) -> None:
    store = TokenStore(tmp_path)
    store.save(stored(Grant.ESCRITURA))
    loaded = store.load(Grant.ESCRITURA)
    assert loaded is not None and loaded.refresh_token.reveal() == REFRESH
    assert store.load(Grant.LECTURA) is None
    assert store.remove(Grant.ESCRITURA) is True
    assert store.remove(Grant.ESCRITURA) is False


def test_damaged_or_mismatched_token_file_is_an_explicit_error(tmp_path: Path) -> None:
    store = TokenStore(tmp_path)
    (tmp_path / "google-lectura.json").write_text("{no es json", encoding="utf-8")
    with pytest.raises(CalendarError, match="dañado"):
        store.load(Grant.LECTURA)
    (tmp_path / "google-lectura.json").write_text(
        json.dumps(
            {
                "grant": "lectura",
                "refresh_token": REFRESH,
                "scope": WRITE_SCOPE,  # un archivo de lectura con el scope de escritura
                "obtained": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CalendarError):
        store.load(Grant.LECTURA)
