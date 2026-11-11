"""Ciclo de vida de los agentes importados (F8-T01, DT-16, ADR-0018).

El estado lo gestiona el backend, nunca el archivo del agente (ADR-0004). Flujo:
importar → borrador → probar → aprobar → activar; desactivar detiene cualquier ejecución.

- Una versión nueva vuelve a borrador: deja de ejecutarse hasta que se pruebe y apruebe.
- La aprobación pertenece a la huella exacta del archivo (sha256), no al número de versión:
  restaurar una versión aprobada antes la deja aprobada (rollback rápido); una nunca aprobada
  vuelve a borrador.
- Nada se borra: las versiones y el historial solo crecen.

Todas las funciones devuelven objetos nuevos.
"""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from lifeos.domain.errors import DomainError

AGENT_ID_RE = re.compile(r"[a-z0-9-]{1,32}")
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
MAX_VERSIONS = 100


class LifecycleError(DomainError):
    """Transición no permitida; el mensaje dice qué hacer."""


class AgentState(StrEnum):
    BORRADOR = "borrador"
    PROBADO = "probado"
    APROBADO = "aprobado"
    ACTIVO = "activo"
    DESACTIVADO = "desactivado"


class LifecycleAction(StrEnum):
    IMPORTAR = "importar"
    PROBAR = "probar"
    APROBAR = "aprobar"
    ACTIVAR = "activar"
    DESACTIVAR = "desactivar"
    RESTAURAR = "restaurar"


@dataclass(frozen=True, slots=True)
class AgentVersion:
    number: int
    sha256: str
    config_version: int  # campo `version` del archivo
    imported_at: datetime


@dataclass(frozen=True, slots=True)
class Transition:
    at: datetime
    action: LifecycleAction
    to_state: AgentState
    version: int


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    agent_id: str
    state: AgentState
    current: int
    versions: tuple[AgentVersion, ...]
    history: tuple[Transition, ...]

    @property
    def current_version(self) -> AgentVersion:
        return self.versions[self.current - 1]

    def version(self, number: int) -> AgentVersion:
        if not 1 <= number <= len(self.versions):
            raise LifecycleError(f"{self.agent_id}: la versión {number} no existe")
        return self.versions[number - 1]


def can_run(entry: RegistryEntry) -> bool:
    """Solo un agente activo ejecuta de verdad (escribe propuestas)."""
    return entry.state is AgentState.ACTIVO


def can_test(entry: RegistryEntry) -> bool:
    """La prueba controlada no escribe, pero un agente desactivado no ejecuta nada."""
    return entry.state is not AgentState.DESACTIVADO


def _check_moment(at: datetime) -> None:
    if at.utcoffset() is None:
        raise LifecycleError("la marca de tiempo necesita zona horaria")


def _check_import(agent_id: str, sha256: str, config_version: int) -> None:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise LifecycleError("id de agente no válido (minúsculas, cifras y guiones; máx. 32)")
    if not _SHA256_RE.fullmatch(sha256):
        raise LifecycleError("huella sha256 no válida")
    if config_version < 1:
        raise LifecycleError("la versión del archivo debe ser 1 o mayor")


def _check_new_version(entry: RegistryEntry, sha256: str, config_version: int) -> None:
    for existing in entry.versions:
        if existing.sha256 == sha256:
            raise LifecycleError(
                f"{entry.agent_id}: ese archivo ya es la v{existing.number}; usa restaurar"
            )
    latest = max(v.config_version for v in entry.versions)
    if config_version <= latest:
        raise LifecycleError(
            f"{entry.agent_id}: sube 'version' en el archivo (la última importada es {latest})"
        )
    if len(entry.versions) >= MAX_VERSIONS:
        raise LifecycleError(f"{entry.agent_id}: máximo {MAX_VERSIONS} versiones")


def register_import(
    entry: RegistryEntry | None,
    agent_id: str,
    sha256: str,
    config_version: int,
    at: datetime,
) -> RegistryEntry:
    """Registra un archivo importado como versión nueva en borrador. Nunca lo activa."""
    _check_moment(at)
    _check_import(agent_id, sha256, config_version)
    if entry is not None:
        if entry.agent_id != agent_id:
            raise LifecycleError(f"el registro de {entry.agent_id} no admite otro agente")
        _check_new_version(entry, sha256, config_version)
    versions = entry.versions if entry is not None else ()
    number = len(versions) + 1
    new_version = AgentVersion(number, sha256, config_version, at)
    transition = Transition(at, LifecycleAction.IMPORTAR, AgentState.BORRADOR, number)
    return RegistryEntry(
        agent_id=agent_id,
        state=AgentState.BORRADOR,
        current=number,
        versions=(*versions, new_version),
        history=(*(entry.history if entry is not None else ()), transition),
    )


def _was_approved(entry: RegistryEntry, number: int) -> bool:
    sha = entry.version(number).sha256
    approved = {t.version for t in entry.history if t.action is LifecycleAction.APROBAR}
    return any(entry.version(n).sha256 == sha for n in approved)


def _target_state(entry: RegistryEntry, action: LifecycleAction) -> AgentState:
    state = entry.state
    if action is LifecycleAction.DESACTIVAR:
        if state is AgentState.DESACTIVADO:
            raise LifecycleError(f"{entry.agent_id} ya está desactivado")
        return AgentState.DESACTIVADO
    if state is AgentState.DESACTIVADO and action is not LifecycleAction.ACTIVAR:
        raise LifecycleError(f"{entry.agent_id} está desactivado: actívalo o restaura una versión")
    if action is LifecycleAction.PROBAR:
        if state not in (AgentState.BORRADOR, AgentState.PROBADO):
            raise LifecycleError(f"{entry.agent_id}: solo se prueba un borrador ({state})")
        return AgentState.PROBADO
    if action is LifecycleAction.APROBAR:
        if state is not AgentState.PROBADO:
            raise LifecycleError(f"{entry.agent_id}: primero hay que probar la versión ({state})")
        return AgentState.APROBADO
    # ACTIVAR
    if state is AgentState.ACTIVO:
        raise LifecycleError(f"{entry.agent_id} ya está activo")
    if not _was_approved(entry, entry.current):
        raise LifecycleError(f"{entry.agent_id}: la versión actual no está aprobada")
    return AgentState.ACTIVO


def _restore(entry: RegistryEntry, version: int | None, at: datetime) -> RegistryEntry:
    if version is None:
        raise LifecycleError(f"{entry.agent_id}: indica la versión que quieres restaurar")
    entry.version(version)  # existe
    if version == entry.current:
        raise LifecycleError(f"{entry.agent_id}: la v{version} ya es la versión actual")
    state = AgentState.APROBADO if _was_approved(entry, version) else AgentState.BORRADOR
    transition = Transition(at, LifecycleAction.RESTAURAR, state, version)
    return replace(entry, state=state, current=version, history=(*entry.history, transition))


def apply_action(
    entry: RegistryEntry,
    action: LifecycleAction,
    at: datetime,
    *,
    version: int | None = None,
) -> RegistryEntry:
    """Aplica una transición del propietario o lanza `LifecycleError` con el motivo."""
    _check_moment(at)
    if action is LifecycleAction.IMPORTAR:
        raise LifecycleError("importar no es una transición: usa register_import")
    if action is LifecycleAction.RESTAURAR:
        return _restore(entry, version, at)
    if version is not None:
        raise LifecycleError("la versión solo se indica al restaurar")
    state = _target_state(entry, action)
    transition = Transition(at, action, state, entry.current)
    return replace(entry, state=state, history=(*entry.history, transition))
