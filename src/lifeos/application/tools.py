"""Herramientas registradas para los agentes (F2-T03, SEC-04).

Cada llamada comprueba que el agente tenga la herramienta en sus permisos efectivos y opera
sobre una `PolicyVault` construida aquí mismo: no existe forma de pasar la bóveda sin envolver.
Las escrituras se auditan antes (`intento`) y después (`ok`, `rechazado` o `error`); si el
`intento` no puede registrarse, la escritura no ocurre. Los rechazos de lectura también se
auditan.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import partial

from lifeos.application.capture import INBOX, capture
from lifeos.application.checkin import append_checkin
from lifeos.application.permissions import PUBLISH_TOOL, TOOL_WRITE_MODES, EffectivePermissions
from lifeos.application.plan_publication import (
    PlanPublication,
    PlanRejectedError,
    PublicationMismatchError,
    check_publication,
)
from lifeos.application.policy_vault import PermissionDeniedError, PolicyVault
from lifeos.application.tasks import LocatedTask, TaskQuery, query_tasks
from lifeos.domain.checkin import CheckInEntry
from lifeos.ports.clock import Clock
from lifeos.ports.config import WriteMode
from lifeos.ports.logs import AuditLog, AuditRecord, AuditResult, LogWriteError
from lifeos.ports.vault import PathRejectedError, Snapshot, VaultPath, VaultStore, Version

_REJECTED_PATH = "(ruta rechazada)"


class AuditIncompleteError(LogWriteError):
    """La escritura SÍ ocurrió, pero no se pudo registrar su resultado. No repetirla a ciegas."""

    def __init__(self, written: str) -> None:
        super().__init__(f"se escribió {written} pero no se pudo auditar el resultado")
        self.written = written


@dataclass(frozen=True, slots=True)
class ActorContext:
    agent_id: str
    correlation_id: str


class ToolRegistry:
    def __init__(
        self,
        vault: VaultStore,
        permissions: Mapping[str, EffectivePermissions],
        audit: AuditLog,
        clock: Clock,
    ) -> None:
        self._vault = vault
        self._permissions = permissions
        self._audit = audit
        self._clock = clock

    # --- Infraestructura común ----------------------------------------------------------------

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

    def _deny(
        self, actor: ActorContext, tool: str, target: str, error: PermissionDeniedError
    ) -> PermissionDeniedError:
        self._log(actor, tool, target, AuditResult.RECHAZADO, error.reason)
        return error

    def _scoped(
        self, actor: ActorContext, tool: str, target: str, mode: WriteMode | None
    ) -> PolicyVault:
        perms = self._permissions.get(actor.agent_id)
        if perms is None or tool not in perms.tools:
            denied = PermissionDeniedError(actor.agent_id, "herramienta_no_autorizada", target)
            raise self._deny(actor, tool, target, denied)
        return PolicyVault(self._vault, perms, mode)

    def _parse(self, actor: ActorContext, tool: str, raw: str) -> VaultPath:
        try:
            return VaultPath.parse(raw)
        except PathRejectedError:
            denied = PermissionDeniedError(actor.agent_id, "ruta_rechazada", _REJECTED_PATH)
            raise self._deny(actor, tool, _REJECTED_PATH, denied) from None

    def _write[T](
        self, actor: ActorContext, tool: str, target: str, operation: Callable[[], T]
    ) -> T:
        self._log(actor, tool, target, AuditResult.INTENTO, None)  # sin este registro no se escribe
        try:
            result = operation()
        except PermissionDeniedError as error:
            raise self._deny(actor, tool, target, error) from None
        except Exception as error:
            # Cualquier fallo cierra el `intento`; la excepción original no se oculta.
            self._log(actor, tool, target, AuditResult.ERROR, type(error).__name__)
            raise
        written = str(result) if isinstance(result, VaultPath) else target
        try:
            self._log(actor, tool, written, AuditResult.OK, None)
        except LogWriteError as error:
            raise AuditIncompleteError(written) from error
        return result

    # --- Herramientas ---------------------------------------------------------------------------

    def leer_nota(self, actor: ActorContext, path: str) -> str:
        tool = "leer_nota"
        target = self._parse(actor, tool, path)
        vault = self._scoped(actor, tool, str(target), TOOL_WRITE_MODES[tool])
        try:
            return vault.read(target).text
        except PermissionDeniedError as error:
            raise self._deny(actor, tool, str(target), error) from None

    def leer_nota_versionada(self, actor: ActorContext, path: str) -> Snapshot:
        """Como `leer_nota`, con la versión para escribir después sin pisar ediciones."""
        tool = "leer_nota"
        target = self._parse(actor, tool, path)
        vault = self._scoped(actor, tool, str(target), TOOL_WRITE_MODES[tool])
        try:
            return vault.read(target)
        except PermissionDeniedError as error:
            raise self._deny(actor, tool, str(target), error) from None

    def listar_tareas(self, actor: ActorContext, query: TaskQuery) -> tuple[LocatedTask, ...]:
        tool = "listar_tareas"
        vault = self._scoped(actor, tool, "(bóveda)", TOOL_WRITE_MODES[tool])
        return query_tasks(vault, query)

    def crear_captura(self, actor: ActorContext, texto: str) -> VaultPath:
        tool = "crear_captura"
        vault = self._scoped(actor, tool, str(INBOX), TOOL_WRITE_MODES[tool])
        now = self._clock.now()
        return self._write(
            actor, tool, str(INBOX), lambda: capture(vault, texto, now=now, source=actor.agent_id)
        )

    def anexar_checkin(self, actor: ActorContext, path: str, entry: CheckInEntry) -> VaultPath:
        tool = "anexar_checkin"
        target = self._parse(actor, tool, path)
        vault = self._scoped(actor, tool, str(target), TOOL_WRITE_MODES[tool])
        return self._write(actor, tool, str(target), lambda: append_checkin(vault, target, entry))

    def publicar_plan(
        self, actor: ActorContext, publication: PlanPublication
    ) -> tuple[VaultPath, ...]:
        """Publica un plan validado (SEC-09): solo el dueño del plan global, solo en modo plan.

        Valida el plan y que el texto le corresponda antes de escribir; un rechazo se audita y
        no escribe nada. Las notas diarias se escriben antes que la semanal, de modo que un
        fallo a mitad deja el plan semanal sin marcar como aprobado.
        """
        tool = PUBLISH_TOOL
        weekly_path = publication.weekly[0]
        vault = self._scoped(actor, tool, str(weekly_path), TOOL_WRITE_MODES[tool])
        try:
            check_publication(publication)
        except PlanRejectedError:
            self._log(actor, tool, str(weekly_path), AuditResult.RECHAZADO, "plan_invalido")
            raise
        except PublicationMismatchError:
            self._log(actor, tool, str(weekly_path), AuditResult.RECHAZADO, "texto_no_coincide")
            raise
        written = []
        for path, text, expected in (*publication.daily, publication.weekly):
            written.append(
                self._write(
                    actor,
                    tool,
                    str(path),
                    partial(_write_note, vault, path, text, expected),
                )
            )
        return tuple(written)


def _write_note(
    vault: VaultStore, path: VaultPath, text: str, expected: Version | None
) -> VaultPath:
    vault.write(path, text, expected=expected)
    return path
