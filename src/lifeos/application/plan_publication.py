"""Publicación de un plan (SEC-09, planificador.md pasos 4 y 7, ADR-0009).

La herramienta `publicar_plan` no recibe texto: valida el plan y escribe el render canónico
de ese plan validado. Así nada que no sea el plan (tareas, secciones o campos colados en una
nota editada a mano) llega a publicarse.
"""

from dataclasses import dataclass
from datetime import date, datetime

from lifeos.application.tasks import ApplicationError
from lifeos.domain.plan import Block, Conflict, PlanningContext, PlanProposal, PlanStatus
from lifeos.domain.plan_note import (
    daily_path,
    render_daily,
    render_weekly,
    revision_path,
    weekly_path,
)
from lifeos.domain.plan_validator import validate_plan
from lifeos.domain.replan import check_history
from lifeos.ports.vault import NoteNotFoundError, VaultPath, VaultStore, Version


@dataclass(frozen=True, slots=True)
class PlanPublication:
    proposal: PlanProposal
    context: PlanningContext
    status: PlanStatus
    generated: datetime
    weekly_expected: Version | None = None  # versión de la nota semanal que se reemplaza
    approved_at: datetime | None = None
    # Revisión de una semana aprobada (ADR-0011): número, nota revisada, corte e historia.
    revision: int = 0
    revises: str | None = None
    cutoff: datetime | None = None
    history: tuple[Block, ...] = ()
    expected_history: tuple[Block, ...] = ()  # historia según el plan revisado
    adjustments: tuple[str, ...] = ()

    @property
    def weekly_target(self) -> str:
        week = self.proposal.week
        return revision_path(week, self.revision) if self.revision else weekly_path(week)


@dataclass(frozen=True, slots=True)
class NoteWrite:
    path: VaultPath
    text: str
    expected: Version | None


class PlanRejectedError(ApplicationError):
    """El plan no es viable: no se guarda. `conflicts` explica cada motivo."""

    def __init__(self, conflicts: tuple[Conflict, ...]) -> None:
        super().__init__(f"plan rechazado: {len(conflicts)} conflicto(s)")
        self.conflicts = conflicts


class PublicationMismatchError(ApplicationError):
    """La publicación pedida no es coherente (p. ej., aprobada sin momento de aprobación)."""


class PartialPublicationError(ApplicationError):
    """Se escribió parte del plan y luego algo falló. `written` dice qué quedó escrito."""

    def __init__(self, written: tuple[VaultPath, ...], cause: Exception) -> None:
        names = ", ".join(str(path) for path in written)
        super().__init__(
            f"publicación incompleta: se escribieron {names} y luego falló "
            f"({type(cause).__name__}); el plan semanal sigue sin aprobar: repite 'aprobar'"
        )
        self.written = written


def check_publication(publication: PlanPublication) -> None:
    """Paso obligatorio antes de escribir: el plan es viable y la petición es coherente."""
    if publication.status is PlanStatus.APROBADO and publication.approved_at is None:
        raise PublicationMismatchError("un plan aprobado necesita el momento de la aprobación")
    if publication.revision and (publication.cutoff is None or publication.revises is None):
        raise PublicationMismatchError("una revisión necesita el corte y la nota que revisa")
    conflicts = validate_plan(publication.proposal, publication.context)
    if publication.revision:
        conflicts += check_history(
            publication.history,
            publication.expected_history,
            publication.proposal.work_blocks(),
        )
    if conflicts:
        raise PlanRejectedError(conflicts)


def _blocks(publication: PlanPublication) -> tuple[Block, ...]:
    return (*publication.history, *publication.proposal.blocks)


def _days(vault: VaultStore, publication: PlanPublication) -> list[date]:
    """Días a publicar. Una revisión nunca reescribe un día anterior al corte."""
    context = publication.context
    with_blocks = {context.local_day(b.interval.start) for b in _blocks(publication)}
    if publication.cutoff is None:
        return sorted(with_blocks)
    first = context.local_day(publication.cutoff)
    # Un día que se quedó sin bloques también se republica para no dejar el plan anterior.
    existing = {d for d in context.week.days() if vault.exists(VaultPath.parse(daily_path(d)))}
    return sorted(d for d in with_blocks | existing if d >= first)


def _daily_write(vault: VaultStore, publication: PlanPublication, day: date) -> NoteWrite:
    path = VaultPath.parse(daily_path(day))
    try:
        existing = vault.read(path)
    except NoteNotFoundError:
        existing = None
    text = render_daily(
        day,
        _blocks(publication),
        zone=publication.context.zone,
        week=publication.proposal.week,
        existing=existing.text if existing else None,
    )
    return NoteWrite(path, text, existing.version if existing else None)


def planned_writes(vault: VaultStore, publication: PlanPublication) -> tuple[NoteWrite, ...]:
    """Notas a escribir, en orden: las diarias (solo si está aprobado) y la semanal al final."""
    weekly = NoteWrite(
        VaultPath.parse(publication.weekly_target),
        render_weekly(
            publication.proposal,
            publication.context,
            status=publication.status,
            generated=publication.generated,
            approved_at=publication.approved_at,
            revision=publication.revision,
            revises=publication.revises,
            cutoff=publication.cutoff,
            history=publication.history,
            adjustments=publication.adjustments,
        ),
        publication.weekly_expected,
    )
    if publication.status is not PlanStatus.APROBADO:
        return (weekly,)
    daily = tuple(_daily_write(vault, publication, day) for day in _days(vault, publication))
    return (*daily, weekly)
