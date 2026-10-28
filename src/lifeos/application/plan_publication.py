"""Publicación de un plan (SEC-09, planificador.md pasos 4 y 7, ADR-0009).

La herramienta `publicar_plan` no recibe texto: valida el plan y escribe el render canónico
de ese plan validado. Así nada que no sea el plan (tareas, secciones o campos colados en una
nota editada a mano) llega a publicarse.
"""

from dataclasses import dataclass
from datetime import date, datetime

from lifeos.application.tasks import ApplicationError
from lifeos.domain.plan import Conflict, PlanningContext, PlanProposal, PlanStatus
from lifeos.domain.plan_note import daily_path, render_daily, render_weekly, weekly_path
from lifeos.domain.plan_validator import validate_plan
from lifeos.ports.vault import NoteNotFoundError, VaultPath, VaultStore, Version


@dataclass(frozen=True, slots=True)
class PlanPublication:
    proposal: PlanProposal
    context: PlanningContext
    status: PlanStatus
    generated: datetime
    weekly_expected: Version | None = None  # versión de la nota semanal que se reemplaza
    approved_at: datetime | None = None


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
    conflicts = validate_plan(publication.proposal, publication.context)
    if conflicts:
        raise PlanRejectedError(conflicts)


def _days(publication: PlanPublication) -> list[date]:
    context = publication.context
    return sorted({context.local_day(b.interval.start) for b in publication.proposal.blocks})


def _daily_write(vault: VaultStore, publication: PlanPublication, day: date) -> NoteWrite:
    path = VaultPath.parse(daily_path(day))
    try:
        existing = vault.read(path)
    except NoteNotFoundError:
        existing = None
    text = render_daily(
        day,
        publication.proposal.blocks,
        zone=publication.context.zone,
        week=publication.proposal.week,
        existing=existing.text if existing else None,
    )
    return NoteWrite(path, text, existing.version if existing else None)


def planned_writes(vault: VaultStore, publication: PlanPublication) -> tuple[NoteWrite, ...]:
    """Notas a escribir, en orden: las diarias (solo si está aprobado) y la semanal al final."""
    weekly = NoteWrite(
        VaultPath.parse(weekly_path(publication.proposal.week)),
        render_weekly(
            publication.proposal,
            publication.context,
            status=publication.status,
            generated=publication.generated,
            approved_at=publication.approved_at,
        ),
        publication.weekly_expected,
    )
    if publication.status is not PlanStatus.APROBADO:
        return (weekly,)
    daily = tuple(_daily_write(vault, publication, day) for day in _days(publication))
    return (*daily, weekly)
