"""Registro de agentes importados (F8-T01, spec registro-de-agentes, ADR-0004, ADR-0018).

Flujo: importar → validar (esquema y solicitado ∩ política) → borrador → prueba controlada →
aprobar → activar; desactivar y restaurar en cualquier momento. Reglas:

- Importar nunca activa ni concede permisos (RULES E.10): la política del propietario decide.
- La política se relee en cada validación: si el propietario la reduce, un agente activo deja de
  ejecutarse aunque su estado siga siendo `activo`.
- Cada versión se guarda tal cual y se comprueba su huella antes de usarla o exportarla: un
  archivo manipulado en disco no se ejecuta.
- Toda operación se audita antes (`intento`) y después; sin el `intento` no cambia nada.
"""

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from lifeos.application.permissions import EffectivePermissions
from lifeos.application.tasks import ApplicationError
from lifeos.domain.agent_lifecycle import (
    LifecycleAction,
    LifecycleError,
    RegistryEntry,
    apply_action,
    can_run,
    can_test,
    register_import,
)
from lifeos.domain.errors import LifeOSError
from lifeos.ports.agent_registry import AgentRegistryStore
from lifeos.ports.clock import Clock
from lifeos.ports.config import AgentConfig, ConfigError
from lifeos.ports.logs import AuditLog, AuditRecord, AuditResult
from lifeos.ports.state import StateError

OWNER_ACTOR = "propietario"
_TARGET_PREFIX = "agentes/"
_UNKNOWN_TARGET = "agentes/(importación)"
HASH_PREFIX_CHARS = 12

ParseAgent = Callable[[bytes, str], AgentConfig]
ValidateAgent = Callable[[AgentConfig], EffectivePermissions]


class AgentNotFoundError(ApplicationError):
    def __init__(self, agent_id: str) -> None:
        super().__init__(f"no hay ningún agente importado con id '{agent_id}'")
        self.agent_id = agent_id


class AgentNotRunnableError(ApplicationError):
    """El estado del agente no permite ejecutarlo (o probarlo)."""


@dataclass(frozen=True, slots=True)
class RegistryView:
    entry: RegistryEntry
    config: AgentConfig | None  # None si la copia de la versión no se pudo leer
    effective: EffectivePermissions | None  # None si hoy la política la rechaza
    problems: tuple[str, ...]


class AgentRegistry:
    def __init__(
        self,
        store: AgentRegistryStore,
        parse: ParseAgent,
        validate: ValidateAgent,
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._store = store
        self._parse = parse
        self._validate = validate
        self._audit = audit
        self._clock = clock

    # --- Auditoría ------------------------------------------------------------------------------

    def _log(
        self,
        correlation: str,
        action: str,
        target: str,
        result: AuditResult,
        reason: str | None = None,
    ) -> None:
        self._audit.append(
            AuditRecord(
                ts=self._clock.now(),
                correlation_id=correlation,
                actor=OWNER_ACTOR,
                action=action,
                target=target,
                result=result,
                reason=reason,
            )
        )

    # --- Lectura --------------------------------------------------------------------------------

    def _entry(self, agent_id: str) -> RegistryEntry:
        entry = self._store.load().get(agent_id)
        if entry is None:
            raise AgentNotFoundError(agent_id)
        return entry

    def _verified_bytes(self, entry: RegistryEntry, number: int) -> bytes:
        version = entry.version(number)
        data = self._store.get_version(entry.agent_id, number)
        if hashlib.sha256(data).hexdigest() != version.sha256:
            raise StateError(
                f"la copia de la v{number} de {entry.agent_id} no coincide con su huella; "
                "no se usa (restaura otra versión o vuelve a importarla)"
            )
        return data

    def _config(self, entry: RegistryEntry) -> AgentConfig:
        data = self._verified_bytes(entry, entry.current)
        return self._parse(data, f"{_TARGET_PREFIX}{entry.agent_id}/v{entry.current}")

    def _view(self, entry: RegistryEntry) -> RegistryView:
        try:
            config = self._config(entry)
        except (StateError, ConfigError) as error:
            return RegistryView(entry, None, None, (str(error),))
        try:
            return RegistryView(entry, config, self._validate(config), ())
        except ConfigError as error:
            return RegistryView(entry, config, None, error.violations)

    def views(self) -> tuple[RegistryView, ...]:
        return tuple(self._view(entry) for _, entry in sorted(self._store.load().items()))

    def export(self, agent_id: str, *, version: int | None = None) -> bytes:
        entry = self._entry(agent_id)
        return self._verified_bytes(entry, entry.current if version is None else version)

    def _usable(
        self, agent_id: str, allowed: Callable[[RegistryEntry], bool], what: str
    ) -> tuple[AgentConfig, EffectivePermissions]:
        entry = self._entry(agent_id)
        if not allowed(entry):
            raise AgentNotRunnableError(
                f"{agent_id} está en estado {entry.state}: no se puede {what}"
            )
        config = self._config(entry)
        return config, self._validate(config)

    def runnable(self, agent_id: str) -> tuple[AgentConfig, EffectivePermissions]:
        """Configuración y permisos de un agente activo, revalidados contra la política actual."""
        return self._usable(agent_id, can_run, "ejecutar")

    def testable(self, agent_id: str) -> tuple[AgentConfig, EffectivePermissions]:
        """Como `runnable`, para la prueba controlada (sin escrituras)."""
        return self._usable(agent_id, can_test, "probar")

    # --- Escritura ------------------------------------------------------------------------------

    def import_config(self, data: bytes, source: str) -> RegistryEntry:
        """Valida y registra un archivo como versión nueva en borrador."""
        correlation = uuid.uuid4().hex
        try:
            config = self._parse(data, source)
            self._validate(config)
        except ConfigError as error:
            self._log(
                correlation,
                "agente_importar",
                _UNKNOWN_TARGET,
                AuditResult.RECHAZADO,
                type(error).__name__,
            )
            raise
        target = f"{_TARGET_PREFIX}{config.id}"
        try:
            entry = register_import(
                self._store.load().get(config.id),
                config.id,
                config.source_sha256,
                config.version,
                self._clock.now(),
            )
        except LifecycleError:
            self._log(correlation, "agente_importar", target, AuditResult.RECHAZADO, "version")
            raise
        reason = f"v{entry.current} sha256:{config.source_sha256[:HASH_PREFIX_CHARS]}"
        self._log(correlation, "agente_importar", target, AuditResult.INTENTO, reason)
        self._commit(correlation, "agente_importar", target, entry, data, reason=reason)
        return entry

    def _commit(
        self,
        correlation: str,
        action: str,
        target: str,
        entry: RegistryEntry,
        data: bytes | None = None,
        *,
        reason: str | None = None,
    ) -> None:
        reason = reason or f"v{entry.current} {entry.state}"
        try:
            if data is not None:
                self._store.put_version(entry.agent_id, entry.current, data)
            self._store.save(entry)
        except LifeOSError as error:
            self._log(correlation, action, target, AuditResult.ERROR, type(error).__name__)
            raise
        self._log(correlation, action, target, AuditResult.OK, reason)

    def record_test(self, agent_id: str) -> RegistryEntry:
        """Marca la versión actual como probada tras una prueba controlada correcta."""
        return self._apply(agent_id, LifecycleAction.PROBAR, None)

    def transition(
        self, agent_id: str, action: LifecycleAction, *, version: int | None = None
    ) -> RegistryEntry:
        """Aprobar, activar, desactivar o restaurar. Probar exige una prueba de verdad."""
        if action is LifecycleAction.PROBAR:
            raise LifecycleError(f"{agent_id}: se marca como probado al ejecutar una prueba")
        return self._apply(agent_id, action, version)

    def _apply(self, agent_id: str, action: LifecycleAction, version: int | None) -> RegistryEntry:
        correlation = uuid.uuid4().hex
        name = f"agente_{action}"
        target = f"{_TARGET_PREFIX}{agent_id}"
        entry = self._entry(agent_id)
        try:
            updated = apply_action(entry, action, self._clock.now(), version=version)
            if action in (LifecycleAction.APROBAR, LifecycleAction.ACTIVAR):
                self._validate(self._config(updated))
            elif action is LifecycleAction.RESTAURAR:
                self._verified_bytes(updated, updated.current)
        except (LifecycleError, ConfigError, StateError) as error:
            self._log(correlation, name, target, AuditResult.RECHAZADO, type(error).__name__)
            raise
        self._log(correlation, name, target, AuditResult.INTENTO)
        self._commit(correlation, name, target, updated)
        return updated
