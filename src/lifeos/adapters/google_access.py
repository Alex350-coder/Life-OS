"""Conectar, consultar y revocar el acceso a Google Calendar (ADR-0017, SEC-13).

Revocar en Google revoca toda la autorización del cliente, así que `revoke` trata los dos
permisos a la vez: los revoca en Google y borra los tokens locales. Si Google no confirma la
revocación de uno, su token local se conserva para poder reintentarlo y el error se informa.
"""

from collections.abc import Callable
from datetime import datetime

from lifeos.adapters.google_oauth import LOGIN_TIMEOUT_S, GoogleOAuth, run_loopback_flow
from lifeos.adapters.google_tokens import Grant, TokenStore
from lifeos.ports.calendar import CalendarError, GrantStatus, RevokeReport


class GoogleCalendarAccess:
    def __init__(
        self,
        oauth: GoogleOAuth,
        store: TokenStore,
        *,
        now: Callable[[], datetime],
        login_timeout_s: float = LOGIN_TIMEOUT_S,
    ) -> None:
        self._oauth = oauth
        self._store = store
        self._now = now
        self._timeout = login_timeout_s

    def connect(self, grant: str, open_browser: Callable[[str], object]) -> GrantStatus:
        try:
            chosen = Grant(grant)
        except ValueError:
            raise CalendarError("permiso desconocido: usa lectura o escritura") from None
        token = run_loopback_flow(
            self._oauth, chosen, open_browser, now=self._now, timeout_s=self._timeout
        )
        self._store.save(token)
        return GrantStatus(chosen.value, chosen.scope, True, token.obtained)

    def status(self) -> tuple[GrantStatus, ...]:
        found = []
        for grant in Grant:
            try:
                token = self._store.load(grant)
            except CalendarError as error:
                found.append(GrantStatus(grant.value, grant.scope, False, None, str(error)))
                continue
            found.append(
                GrantStatus(
                    grant.value,
                    grant.scope,
                    token is not None,
                    token.obtained if token is not None else None,
                )
            )
        return tuple(found)

    def revoke(self) -> RevokeReport:
        revoked: list[str] = []
        invalid: list[str] = []
        removed: list[str] = []
        failures: list[str] = []
        for grant in Grant:
            try:
                token = self._store.load(grant)
                if token is None:
                    continue
                (revoked if self._oauth.revoke(token) else invalid).append(grant.value)
            except CalendarError as error:
                failures.append(f"{grant.value}: {error}")
                continue  # se conserva el token local para reintentar
            if self._store.remove(grant):
                removed.append(grant.value)
        if failures:
            done = f" Ya revocado: {', '.join(revoked)}." if revoked else ""
            raise CalendarError(
                "no se pudo revocar en Google (" + "; ".join(failures) + "); el token local se "
                "conserva para reintentarlo (si el archivo está dañado, bórralo). Quita también "
                f"el acceso en https://myaccount.google.com/permissions.{done}"
            )
        # El id del calendario Life OS se conserva: al volver a conectar se reutiliza (si ya no
        # es accesible, el adaptador lo detecta con un 404 y crea otro).
        return RevokeReport(tuple(revoked), tuple(invalid), tuple(removed))
