"""Herramientas del calendario externo (ADR-0017, SEC-04, SEC-13).

Como `ToolRegistry`, cada llamada comprueba la herramienta en los permisos efectivos del agente:
`leer_calendario` para la disponibilidad y `publicar_calendario` (solo el dueño del plan global)
para escribir. Cada escritura externa se audita antes (`intento`) y después; sin el `intento`
no se escribe. El destino auditado es la clave del evento, nunca el título de la tarea.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import partial

from lifeos.application.permissions import (
    CALENDAR_READ_TOOL,
    CALENDAR_WRITE_TOOL,
    EffectivePermissions,
)
from lifeos.application.policy_vault import PermissionDeniedError
from lifeos.application.tools import ActorContext
from lifeos.domain.calendar_sync import PublishedEntry, SyncPlan
from lifeos.domain.external import ExternalBusy
from lifeos.ports.calendar import BusyReader, CalendarError, CalendarWriter
from lifeos.ports.clock import Clock
from lifeos.ports.logs import AuditLog, AuditRecord, AuditResult

KEY_IN_AUDIT = 12
NO_WRITER = (
    "no hay permiso de escritura en Google Calendar: autorízalo con "
    "'lifeos-calendario conectar --escritura'"
)


class PartialCalendarSyncError(CalendarError):
    """Parte de la publicación se hizo y luego algo falló. Repetirla es seguro (idempotente)."""

    def __init__(self, done: int, cause: Exception) -> None:
        super().__init__(
            f"publicación en el calendario incompleta: {done} cambio(s) hecho(s) antes del fallo "
            f"({cause}); repite la publicación: no crea duplicados"
        )
        self.done = done


@dataclass(frozen=True, slots=True)
class AppliedSync:
    created: int
    updated: int
    deleted: int


class CalendarTools:
    def __init__(
        self,
        permissions: Mapping[str, EffectivePermissions],
        audit: AuditLog,
        clock: Clock,
        *,
        reader: BusyReader | None = None,
        writer: CalendarWriter | None = None,
    ) -> None:
        self._permissions = permissions
        self._audit = audit
        self._clock = clock
        self._reader = reader
        self._writer = writer

    @property
    def can_read(self) -> bool:
        return self._reader is not None

    @property
    def can_write(self) -> bool:
        return self._writer is not None

    def _log(
        self, actor: ActorContext, tool: str, target: str, result: AuditResult, reason: str | None
    ) -> None:
        self._audit.append(
            AuditRecord(
                ts=self._clock.now(),
                correlation_id=actor.correlation_id,
                actor=actor.agent_id,
                action=tool,
                target=target,
                result=result,
                reason=reason,
            )
        )

    def _require(self, actor: ActorContext, tool: str, target: str) -> None:
        perms = self._permissions.get(actor.agent_id)
        if perms is None or tool not in perms.tools:
            reason = "herramienta_no_autorizada"
            self._log(actor, tool, target, AuditResult.RECHAZADO, reason)
            raise PermissionDeniedError(actor.agent_id, reason, target)

    # --- Lectura ------------------------------------------------------------------------------

    def busy(self, actor: ActorContext, start: datetime, end: datetime) -> tuple[ExternalBusy, ...]:
        """Intervalos ocupados; sin lector configurado (no autorizado), ninguno."""
        target = "calendario:disponibilidad"
        self._require(actor, CALENDAR_READ_TOOL, target)
        if self._reader is None:
            return ()
        try:
            return self._reader.busy(start, end)
        except Exception as error:
            self._log(actor, CALENDAR_READ_TOOL, target, AuditResult.ERROR, type(error).__name__)
            raise

    # --- Escritura ----------------------------------------------------------------------------

    def _writer_for(self, actor: ActorContext, target: str) -> CalendarWriter:
        self._require(actor, CALENDAR_WRITE_TOOL, target)
        if self._writer is None:
            raise CalendarError(NO_WRITER)
        return self._writer

    def published(
        self, actor: ActorContext, week_label: str, start: datetime, end: datetime
    ) -> tuple[PublishedEntry, ...]:
        writer = self._writer_for(actor, f"calendario:{week_label}")
        return writer.list_entries(week_label, start, end)

    def _write(self, actor: ActorContext, target: str, operation: Callable[[], None]) -> None:
        tool = CALENDAR_WRITE_TOOL
        self._log(actor, tool, target, AuditResult.INTENTO, None)  # sin él no se escribe
        try:
            operation()
        except Exception as error:
            self._log(actor, tool, target, AuditResult.ERROR, type(error).__name__)
            raise
        self._log(actor, tool, target, AuditResult.OK, None)

    def apply(self, actor: ActorContext, week_label: str, sync: SyncPlan) -> AppliedSync:
        """Aplica el plan de sincronización en orden: crear, actualizar y borrar."""
        writer = self._writer_for(actor, f"calendario:{week_label}")

        def target(key: str) -> str:
            return f"calendario:{week_label}:{key[:KEY_IN_AUDIT]}"

        steps: list[tuple[str, Callable[[], None]]] = []
        for entry in sync.create:
            steps.append((target(entry.key), partial(writer.create, week_label, entry)))
        for event_id, entry in sync.update:
            steps.append((target(entry.key), partial(writer.update, event_id, week_label, entry)))
        for event in sync.delete:
            steps.append((target(event.key), partial(writer.delete, event.event_id)))
        for done, (name, operation) in enumerate(steps):
            try:
                self._write(actor, name, operation)
            except Exception as error:
                if done:
                    raise PartialCalendarSyncError(done, error) from error
                raise
        return AppliedSync(len(sync.create), len(sync.update), len(sync.delete))
