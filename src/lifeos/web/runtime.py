"""Dependencias de la web: el `Runtime` de la raíz de composición, igual que en la CLI.

El runtime sin modelo se construye al arrancar (si la configuración no es válida, el servidor no
arranca). El que usa el modelo se construye la primera vez que se pide `ia` y exige la clave del
proveedor en ese momento. Las operaciones que escriben se serializan con un cerrojo: hay un solo
propietario, y así dos pestañas no compiten por la misma nota.
"""

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from lifeos.application.closing import DailyClosing
from lifeos.application.coach import CoachSession
from lifeos.application.jefe import JefePlanner
from lifeos.bootstrap import Runtime, Settings, build_runtime
from lifeos.ports.clock import Clock
from lifeos.ports.vault import VaultStore
from lifeos.web.security import Monotonic, RateLimiter

# Peticiones con IA por minuto: cada una es una llamada de pago al proveedor.
AI_RATE_LIMIT = 6


class SetupError(Exception):
    """Falta algo en la configuración local para atender la petición."""


class RateLimitedError(Exception):
    def __init__(self, message: str, *, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class WebRuntime:
    def __init__(
        self,
        settings: Settings,
        *,
        clock: Clock | None = None,
        monotonic: Monotonic = time.monotonic,
    ) -> None:
        self._settings = settings
        self._ai_rate = RateLimiter(monotonic, limit=AI_RATE_LIMIT)
        self._clock = clock
        self._base = build_runtime(settings, clock=clock)
        self._llm: Runtime | None = None
        self._build_lock = threading.Lock()
        self._write_lock = threading.Lock()

    @property
    def base(self) -> Runtime:
        return self._base

    def runtime(self, *, use_ai: bool) -> Runtime:
        if not use_ai:
            return self._base
        allowed, retry = self._ai_rate.allow("ia")
        if not allowed:
            raise RateLimitedError(
                "demasiadas peticiones con IA; espera un momento o repite sin IA", retry_after=retry
            )
        with self._build_lock:
            if self._llm is None:
                self._llm = build_runtime(self._settings, with_llm=True, clock=self._clock)
            return self._llm

    @contextmanager
    def writing(self) -> Iterator[None]:
        with self._write_lock:
            yield


def vault(runtime: Runtime) -> VaultStore:
    if runtime.vault is None:
        raise SetupError("falta LIFEOS_VAULT_PATH (ruta a tu bóveda) en el entorno o en .env")
    return runtime.vault


def jefe(runtime: Runtime) -> JefePlanner:
    if runtime.jefe is None:
        raise SetupError("no hay agente Jefe configurado (config/agents/jefe.yaml y la política)")
    return runtime.jefe


def coach(runtime: Runtime) -> CoachSession:
    if runtime.coach is None:
        raise SetupError("no hay agente Coach configurado (config/agents/coach.yaml y la política)")
    return runtime.coach


def closing(runtime: Runtime) -> DailyClosing:
    if runtime.closing is None:
        raise SetupError("el cierre necesita la bóveda y el agente Coach configurados")
    return runtime.closing
