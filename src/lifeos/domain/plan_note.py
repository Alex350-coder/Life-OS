"""Notas de plan en la bóveda (ADR-0009): `Semanal/AAAA-Wnn.md` y `Diario/AAAA-MM-DD.md`.

Los bloques se escriben como líneas analizables `- HH:MM–HH:MM · título [id:: t-…]` (o
`[tipo:: fijo|protegido]`) para que un plan editado a mano se pueda volver a validar antes de
aprobarlo. Todo texto libre (títulos, motivos, justificación) se reduce a una línea sin
corchetes: nunca puede crear tareas, campos ni secciones (SEC-10). Cualquier nota que no siga
el formato se rechaza (fallo cerrado).
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lifeos.domain.capacity import Interval, local_datetime
from lifeos.domain.checkin import CHECKIN_HEADING
from lifeos.domain.errors import DomainError, ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.note import split_lines
from lifeos.domain.plan import (
    Block,
    BlockKind,
    PlanningContext,
    PlanOrigin,
    PlanProposal,
    PlanStatus,
    clean,
)
from lifeos.domain.schedule import TimeRange, Weekday
from lifeos.domain.sections import section_lines
from lifeos.domain.weeks import IsoWeek

MAX_TEXT_CHARS = 2_000
MAX_REVISIONS = 20
ADJUSTMENTS_HEADING = "Ajustes atendidos"
JUSTIFICATION_HEADING = "Justificación"
NO_TEXT = "—"
BLOCKS_HEADING = "Bloques por día"
DAILY_BLOCK_SECTIONS = ("Lo no negociable", "Bloques", "Tiempo protegido")
CLOSING_HEADING = "Cierre"
_KIND_TAG = {BlockKind.FIJO: "fijo", BlockKind.PROTEGIDO: "protegido"}
_TAG_KIND = {tag: kind for kind, tag in _KIND_TAG.items()}
_BLOCK_RE = re.compile(r"- (\d{2}:\d{2})–(\d{2}:\d{2}) · (.+?) \[(id|tipo):: ([^\]]*)\]")
_DAY_HEADING_RE = re.compile(r"### \S+ (\d{4}-\d{2}-\d{2})")
_FIELD_RE = re.compile(r"(\w+): (.*)")


class PlanNoteError(DomainError):
    """La nota de plan no sigue el formato: no se aprueba ni se publica nada a partir de ella."""


def weekly_path(week: IsoWeek) -> str:
    return f"Semanal/{week.render()}.md"


def daily_path(day: date) -> str:
    return f"Diario/{day.isoformat()}.md"


def revision_path(week: IsoWeek, number: int) -> str:
    """Revisión N del plan de la semana (ADR-0011): `Semanal/AAAA-Wnn-rN.md`, N de 1 a 20."""
    if not 1 <= number <= MAX_REVISIONS:
        raise ValueError(f"la revisión debe estar entre 1 y {MAX_REVISIONS}")
    return f"Semanal/{week.render()}-r{number}.md"


def is_approved(text: str) -> bool:
    """La nota declara `estado_plan: aprobado` en su frontmatter (sin analizar el resto)."""
    lines = split_lines(text.removeprefix("﻿"))
    if not lines or lines[0] != "---":
        return False
    for line in lines[1:]:
        if line == "---":
            return False
        if line.strip() == f"estado_plan: {PlanStatus.APROBADO.value}":
            return True
    return False


# --- Renderizado ------------------------------------------------------------------------------


def _hhmm(moment: datetime, zone: ZoneInfo, day: date) -> str:
    local = moment.astimezone(zone)
    if local.date() > day and local.hour == 0 and local.minute == 0:
        return "24:00"
    return f"{local:%H:%M}"


def format_span(interval: Interval, zone: ZoneInfo) -> str:
    """`HH:MM–HH:MM` en hora local; la medianoche siguiente se escribe `24:00`."""
    day = interval.start.astimezone(zone).date()
    return f"{_hhmm(interval.start, zone, day)}–{_hhmm(interval.end, zone, day)}"


def _span_text(block: Block, zone: ZoneInfo) -> str:
    return format_span(block.interval, zone)


def _block_line(block: Block, zone: ZoneInfo) -> str:
    tag = f"[id:: {block.task_id}]" if block.task_id else f"[tipo:: {_KIND_TAG[block.kind]}]"
    return f"- {_span_text(block, zone)} · {clean(block.title)} {tag}"


def _day_label(day: date) -> str:
    return f"{Weekday.of(day).render()} {day.isoformat()}"


def _of_day(blocks: Iterable[Block], day: date, zone: ZoneInfo) -> list[Block]:
    chosen = [b for b in blocks if b.interval.start.astimezone(zone).date() == day]
    return sorted(chosen, key=lambda b: b.interval.start.timestamp())


def _bullets(items: Sequence[str], empty: str) -> list[str]:
    return [f"- {item}" for item in items] if items else [f"- {empty}"]


def render_weekly(
    proposal: PlanProposal,
    context: PlanningContext,
    *,
    status: PlanStatus,
    generated: datetime,
    approved_at: datetime | None = None,
    revision: int = 0,
    revises: str | None = None,
    cutoff: datetime | None = None,
    history: Sequence[Block] = (),
    adjustments: Sequence[str] = (),
) -> str:
    """Nota semanal. Una revisión (ADR-0011) añade `revision`, `revisa` y `desde`, la historia
    anterior al corte en sus días y la sección de ajustes atendidos."""
    zone = context.zone
    approval = [f"aprobado: {approved_at.isoformat()}"] if approved_at is not None else []
    if revision and (revises is None or cutoff is None):
        raise ValidationError("revisión", revision, "necesita la nota que revisa y el corte")
    revision_fields = (
        [f"revision: {revision}", f"revisa: {revises}", f"desde: {cutoff.isoformat()}"]
        if revision and revises is not None and cutoff is not None
        else []
    )
    blocks = (*history, *proposal.blocks)
    lines = [
        "---",
        "tipo: semanal",
        f"semana: {proposal.week.render()}",
        f"estado_plan: {status.value}",
        *approval,
        *revision_fields,
        "generado_por: jefe",
        f"origen: {proposal.origin.value}",
        f"generado: {generated.isoformat()}",
        f"zona: {zone.key}",
        "---",
        "",
        f"# Plan semanal {proposal.week.render()}",
        "",
        "## Capacidad disponible",
    ]
    lines += [
        f"- {_day_label(d.day)} · libre {d.free_minutes} min · trabajo hasta {d.budget_minutes} min"
        for d in context.days
    ]
    lines += ["", f"## {BLOCKS_HEADING}"]
    for day in proposal.week.days():
        lines.append(f"### {_day_label(day)}")
        lines += [_block_line(b, zone) for b in _of_day(blocks, day, zone)]
        lines.append("")
    protected = [b for b in proposal.blocks if b.kind is BlockKind.PROTEGIDO]
    lines.append("## Tiempo protegido reservado")
    lines += _bullets(
        [
            f"{_day_label(b.interval.start.astimezone(zone).date())} · "
            f"{_span_text(b, zone)} · {clean(b.title)}"
            for b in sorted(protected, key=lambda b: b.interval.start.timestamp())
        ],
        "Ninguno",
    )
    lines += ["", "## Tareas que no caben y propuesta de aplazamiento"]
    lines += _bullets(
        [
            f"{clean(u.title)} ({u.task_id or 'sin id'}) · {u.minutes} min · "
            f"{clean(u.reason)} · {clean(u.suggestion)}"
            for u in proposal.unplanned
        ],
        "Ninguna",
    )
    lines += ["", "## Conflictos y alertas"]
    lines += _bullets([clean(alert) for alert in proposal.alerts], "Ninguno")
    if revision:
        lines += ["", f"## {ADJUSTMENTS_HEADING}"]
        lines += _bullets([clean(item) for item in adjustments], "Ninguno")
    lines += [
        "",
        f"## {JUSTIFICATION_HEADING}",
        clean(proposal.justification, MAX_TEXT_CHARS) or NO_TEXT,
        "",
    ]
    return "\n".join(lines)


def render_daily(
    day: date,
    blocks: Iterable[Block],
    *,
    zone: ZoneInfo,
    week: IsoWeek,
    existing: str | None,
) -> str:
    """Nota diaria del plan aprobado; conserva tal cual `Check-ins` y `Cierre` si ya existían."""
    today = _of_day(blocks, day, zone)
    lines = [
        "---",
        "tipo: diario",
        f"fecha: {day.isoformat()}",
        "estado_plan: aprobado",
        "generado_por: jefe",
        f"semana: {week.render()}",
        "---",
        "",
        f"# Plan del día {day.isoformat()}",
        "",
    ]
    groups = (
        [b for b in today if b.kind is BlockKind.FIJO],
        [b for b in today if b.kind is BlockKind.TRABAJO],
        [b for b in today if b.kind is BlockKind.PROTEGIDO],
    )
    for heading, group in zip(DAILY_BLOCK_SECTIONS, groups, strict=True):
        lines += [f"## {heading}", *(_block_line(b, zone) for b in group), ""]
    for heading in (CHECKIN_HEADING, CLOSING_HEADING):
        kept = section_lines(existing, heading) if existing is not None else None
        lines += [*(kept or (f"## {heading}",)), ""]
    # Secciones propias del propietario en una nota ya existente: se conservan tal cual.
    for heading in _other_sections(existing):
        lines += [*(section_lines(existing or "", heading) or ()), ""]
    return "\n".join(lines)


_OWN_SECTIONS = {h.casefold() for h in (*DAILY_BLOCK_SECTIONS, CHECKIN_HEADING, CLOSING_HEADING)}
_H2_RE = re.compile(r"## (.+?)\s*")


def _other_sections(existing: str | None) -> list[str]:
    if existing is None:
        return []
    headings: list[str] = []
    for line in split_lines(existing):
        match = _H2_RE.fullmatch(line)
        if match and match[1].casefold() not in _OWN_SECTIONS and match[1] not in headings:
            headings.append(match[1])
    return headings


# --- Análisis ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WeeklyNote:
    week: IsoWeek
    status: PlanStatus
    origin: PlanOrigin
    generated: datetime
    zone: ZoneInfo
    blocks: tuple[Block, ...]
    justification: str
    revision: int = 0
    revises: str | None = None
    cutoff: datetime | None = None  # corte de una revisión: lo anterior es historia

    def history(self) -> tuple[Block, ...]:
        """Trabajo que empezó antes del corte: ya ocurrió y no se vuelve a validar."""
        if self.cutoff is None:
            return ()
        cut = self.cutoff.timestamp()
        return tuple(
            b
            for b in self.blocks
            if b.kind is BlockKind.TRABAJO and b.interval.start.timestamp() < cut
        )

    def plan_blocks(self) -> tuple[Block, ...]:
        """Bloques del plan que se validan: todo menos la historia."""
        past = set(self.history())
        return tuple(b for b in self.blocks if b not in past)


def _frontmatter(lines: Sequence[str]) -> tuple[dict[str, str], int]:
    if not lines or lines[0] != "---":
        raise PlanNoteError("la nota de plan no tiene frontmatter")
    fields: dict[str, str] = {}
    for index, line in enumerate(lines[1:], start=1):
        if line == "---":
            return fields, index + 1
        match = _FIELD_RE.fullmatch(line)
        if match is None or match[1] in fields:
            raise PlanNoteError(f"frontmatter no válido en la línea {index + 1}")
        fields[match[1]] = match[2].strip()
    raise PlanNoteError("frontmatter sin cerrar")


def _required(fields: dict[str, str], key: str) -> str:
    if key not in fields:
        raise PlanNoteError(f"falta '{key}' en el frontmatter")
    return fields[key]


def _zone(text: str) -> ZoneInfo:
    try:
        return ZoneInfo(text)
    except (ZoneInfoNotFoundError, ValueError):
        raise PlanNoteError(f"zona '{text}' desconocida") from None


def _block(line: str, day: date, zone: ZoneInfo) -> Block:
    match = _BLOCK_RE.fullmatch(line)
    if match is None:
        raise PlanNoteError(f"línea de bloque no válida: {clean(line, 80)!r}")
    start, end, title, key, value = match.groups()
    try:
        span = TimeRange.parse(f"{start}–{end}")
        interval = Interval(
            local_datetime(day, span.start, zone), local_datetime(day, span.end, zone)
        )
        if key == "id":
            return Block(BlockKind.TRABAJO, title, interval, TaskId(value.strip()))
        kind = _TAG_KIND.get(value.strip())
        if kind is None:
            raise PlanNoteError(f"tipo de bloque desconocido: {clean(value, 40)!r}")
        return Block(kind, title, interval)
    except ValidationError as error:
        raise PlanNoteError(f"bloque no válido ({error.field}): {error.reason}") from None


def _section(lines: Sequence[str], heading: str) -> list[str]:
    title = f"## {heading}"
    starts = [index for index, line in enumerate(lines) if line.rstrip() == title]
    if not starts:
        raise PlanNoteError(f"falta la sección '{heading}'")
    if len(starts) > 1:
        raise PlanNoteError(f"la sección '{heading}' está duplicada")
    start = starts[0]
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return list(lines[start + 1 : end])


def _parse_day(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise PlanNoteError(f"fecha de día no válida: {text}") from None


def _weekly_blocks(lines: Sequence[str], zone: ZoneInfo) -> tuple[Block, ...]:
    blocks: list[Block] = []
    day: date | None = None
    for line in _section(lines, BLOCKS_HEADING):
        if not line.strip():
            continue
        heading = _DAY_HEADING_RE.fullmatch(line)
        if heading:
            day = _parse_day(heading[1])
            continue
        if day is None:
            raise PlanNoteError("bloque fuera de un día (### día AAAA-MM-DD)")
        blocks.append(_block(line, day, zone))
    return tuple(blocks)


def _enum[E: (PlanStatus, PlanOrigin)](kind: type[E], text: str, field: str) -> E:
    try:
        return kind(text)
    except ValueError:
        raise PlanNoteError(f"{field} '{clean(text, 40)}' no válido") from None


def parse_weekly(text: str) -> WeeklyNote:
    lines = split_lines(text.removeprefix("﻿"))
    fields, _ = _frontmatter(lines)
    if _required(fields, "tipo") != "semanal":
        raise PlanNoteError("la nota no es un plan semanal")
    try:
        week = IsoWeek.parse(_required(fields, "semana"))
        generated = datetime.fromisoformat(_required(fields, "generado"))
    except (ValueError, ValidationError) as error:
        raise PlanNoteError(f"frontmatter no válido: {error}") from None
    if generated.utcoffset() is None:
        raise PlanNoteError("'generado' requiere zona horaria")
    zone = _zone(_required(fields, "zona"))
    revision, revises, cutoff = _revision(fields)
    return WeeklyNote(
        week=week,
        status=_enum(PlanStatus, _required(fields, "estado_plan"), "estado_plan"),
        origin=_enum(PlanOrigin, _required(fields, "origen"), "origen"),
        generated=generated,
        zone=zone,
        blocks=_weekly_blocks(lines, zone),
        justification=_justification(lines),
        revision=revision,
        revises=revises,
        cutoff=cutoff,
    )


_REVISION_RE = re.compile(r"[1-9][0-9]?")
_REVISES_RE = re.compile(r"Semanal/[0-9]{4}-W[0-9]{2}(-r[1-9][0-9]?)?\.md")


def _revision(fields: dict[str, str]) -> tuple[int, str | None, datetime | None]:
    if "revision" not in fields:
        if "revisa" in fields or "desde" in fields:
            raise PlanNoteError("'revisa' y 'desde' solo van en una revisión")
        return 0, None, None
    text = fields["revision"]
    if not _REVISION_RE.fullmatch(text) or int(text) > MAX_REVISIONS:
        raise PlanNoteError(f"revision debe ser un número de 1 a {MAX_REVISIONS}")
    revises = _required(fields, "revisa")
    if not _REVISES_RE.fullmatch(revises):
        raise PlanNoteError("'revisa' debe nombrar una nota semanal (Semanal/AAAA-Wnn[-rN].md)")
    try:
        cutoff = datetime.fromisoformat(_required(fields, "desde"))
    except ValueError:
        raise PlanNoteError("'desde' no es una fecha y hora ISO") from None
    if cutoff.utcoffset() is None:
        raise PlanNoteError("'desde' requiere zona horaria")
    return int(text), revises, cutoff


def _justification(lines: Sequence[str]) -> str:
    text = " ".join(line.strip() for line in _section(lines, JUSTIFICATION_HEADING) if line.strip())
    return "" if text == NO_TEXT else clean(text, MAX_TEXT_CHARS)


def parse_daily_blocks(text: str, day: date, zone: ZoneInfo) -> tuple[Block, ...]:
    lines = split_lines(text.removeprefix("﻿"))
    blocks: list[Block] = []
    for heading in DAILY_BLOCK_SECTIONS:
        blocks += [_block(line, day, zone) for line in _section(lines, heading) if line.strip()]
    return tuple(blocks)
