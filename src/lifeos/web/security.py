"""Primitivas de acceso local (ADR-0013, SEC-11): token de arranque, sesiones y límite de tasa.

Todo vive en memoria del proceso: reiniciar el servidor invalida tokens y sesiones. El reloj es
monotónico e inyectable para poder probar la caducidad sin esperar.
"""

import hmac
import math
import secrets
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

LOOPBACK = "127.0.0.1"
LAUNCH_TOKEN_TTL_S = 5 * 60
SESSION_TTL_S = 12 * 60 * 60
SESSION_COOKIE = "lifeos_session"
CSRF_HEADER = "X-CSRF-Token"
TOKEN_BYTES = 32  # 256 bits
AUTH_RATE_LIMIT = 10  # intentos por minuto en /auth
API_RATE_LIMIT = 240  # peticiones por minuto en /api
RATE_WINDOW_S = 60

Monotonic = Callable[[], float]


def check_bind_host(host: str) -> None:
    """Solo se escucha en la interfaz de bucle local IPv4: nada queda expuesto a la red."""
    if host != LOOPBACK:
        raise ValueError(f"lifeos-web solo escucha en {LOOPBACK} (se pidió {host!r})")


def allowed_hosts(port: int) -> frozenset[str]:
    return frozenset({f"{LOOPBACK}:{port}", f"localhost:{port}"})


def allowed_origins(port: int) -> frozenset[str]:
    return frozenset(f"http://{host}" for host in allowed_hosts(port))


def same_secret(candidate: str, expected: str) -> bool:
    """Comparación en tiempo constante (sobre bytes: admite cualquier texto recibido)."""
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


class LaunchToken:
    """Token de un solo uso que se muestra en la terminal al arrancar y caduca a los 5 min."""

    def __init__(
        self, clock: Monotonic, *, value: str | None = None, ttl_s: float = LAUNCH_TOKEN_TTL_S
    ) -> None:
        self._value = value or secrets.token_urlsafe(TOKEN_BYTES)
        self._expires = clock() + ttl_s
        self._clock = clock
        self._used = False
        self._lock = threading.Lock()

    @property
    def value(self) -> str:
        return self._value

    def consume(self, candidate: str) -> bool:
        with self._lock:
            valid = same_secret(candidate, self._value)
            if not valid or self._used or self._clock() > self._expires:
                return False
            self._used = True
            return True


@dataclass(frozen=True, slots=True)
class Session:
    id: str
    csrf: str
    expires: float


class SessionStore:
    def __init__(self, clock: Monotonic, *, ttl_s: float = SESSION_TTL_S) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self) -> Session:
        session = Session(
            id=secrets.token_urlsafe(TOKEN_BYTES),
            csrf=secrets.token_urlsafe(TOKEN_BYTES),
            expires=self._clock() + self._ttl_s,
        )
        with self._lock:
            now = self._clock()
            self._sessions = {k: s for k, s in self._sessions.items() if s.expires >= now}
            self._sessions[session.id] = session
        return session

    def active(self) -> int:
        with self._lock:
            return len(self._sessions)

    def get(self, session_id: str | None) -> Session | None:
        if not session_id:
            return None
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if self._clock() > session.expires:
                del self._sessions[session_id]
                return None
            return session

    def revoke(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)


class RateLimiter:
    """Ventana deslizante por clave. Devuelve si se permite y, si no, cuántos segundos esperar."""

    def __init__(self, clock: Monotonic, *, limit: int, window_s: float = RATE_WINDOW_S) -> None:
        self._clock = clock
        self._limit = limit
        self._window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = self._clock()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] >= self._window_s:
                hits.popleft()
            if len(hits) >= self._limit:
                return False, max(1, math.ceil(self._window_s - (now - hits[0])))
            hits.append(now)
            return True, 0
