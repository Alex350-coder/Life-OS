"""Comprobaciones de la publicación de un plan (SEC-09, planificador.md paso 4 y 7).

La herramienta `publicar_plan` llama a `check_publication` antes de escribir nada:
- el plan pasa el validador determinista (el paso no se puede omitir);
- el texto que se va a escribir describe exactamente ese plan validado;
- las notas diarias solo se publican junto con un plan semanal aprobado.
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from lifeos.application.tasks import ApplicationError
from lifeos.domain.plan import Block, BlockKind, Conflict, PlanningContext, PlanProposal, PlanStatus
from lifeos.domain.plan_note import (
    PlanNoteError,
    clean,
    daily_path,
    parse_daily_blocks,
    parse_weekly,
    weekly_path,
)
from lifeos.domain.plan_validator import validate_plan
from lifeos.ports.vault import VaultPath, Version

NoteWrite = tuple[VaultPath, str, Version | None]  # ruta, texto nuevo, versión esperada


@dataclass(frozen=True, slots=True)
class PlanPublication:
    proposal: PlanProposal
    context: PlanningContext
    weekly: NoteWrite
    daily: tuple[NoteWrite, ...] = ()


class PlanRejectedError(ApplicationError):
    """El plan no es viable: no se guarda. `conflicts` explica cada motivo."""

    def __init__(self, conflicts: tuple[Conflict, ...]) -> None:
        super().__init__(f"plan rechazado: {len(conflicts)} conflicto(s)")
        self.conflicts = conflicts


class PublicationMismatchError(ApplicationError):
    """El texto a escribir no corresponde al plan validado (o no está aprobado)."""


def _signature(block: Block) -> tuple[BlockKind, str, float, float, str | None]:
    return (
        block.kind,
        clean(block.title),
        block.interval.start.timestamp(),
        block.interval.end.timestamp(),
        block.task_id.value if block.task_id else None,
    )


def _same_blocks(found: Iterable[Block], expected: Iterable[Block]) -> bool:
    return Counter(map(_signature, found)) == Counter(map(_signature, expected))


def _check_weekly(publication: PlanPublication) -> PlanStatus:
    path, text, _ = publication.weekly
    proposal = publication.proposal
    if str(path) != weekly_path(proposal.week):
        raise PublicationMismatchError(f"{path} no es la nota de la semana {proposal.week}")
    try:
        note = parse_weekly(text)
    except PlanNoteError as error:
        raise PublicationMismatchError(f"la nota semanal no es válida: {error}") from None
    if note.week != proposal.week or not _same_blocks(note.blocks, proposal.blocks):
        raise PublicationMismatchError("la nota semanal no coincide con el plan validado")
    return note.status


def _check_daily(publication: PlanPublication, write: NoteWrite) -> None:
    path, text, _ = write
    context = publication.context
    day = next((d for d in context.week.days() if daily_path(d) == str(path)), None)
    if day is None:
        raise PublicationMismatchError(f"{path} no es un día de la semana {context.week}")
    try:
        found = parse_daily_blocks(text, day, context.zone)
    except PlanNoteError as error:
        raise PublicationMismatchError(f"la nota diaria {path} no es válida: {error}") from None
    expected = [b for b in publication.proposal.blocks if _local_day(b, context) == day]
    if not _same_blocks(found, expected):
        raise PublicationMismatchError(f"la nota diaria {path} no coincide con el plan validado")


def _local_day(block: Block, context: PlanningContext) -> date:
    return context.local_day(block.interval.start)


def check_publication(publication: PlanPublication) -> None:
    conflicts = validate_plan(publication.proposal, publication.context)
    if conflicts:
        raise PlanRejectedError(conflicts)
    status = _check_weekly(publication)
    if publication.daily and status is not PlanStatus.APROBADO:
        raise PublicationMismatchError("las notas diarias solo se publican con el plan aprobado")
    for write in publication.daily:
        _check_daily(publication, write)
