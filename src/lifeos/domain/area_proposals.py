"""Propuestas de un especialista al Jefe (F8-T02, RULES C.3, spec agentes-especialistas).

Un especialista no reserva tiempo ni cambia prioridades: escribe una nota nueva en
`Propuestas/<semana>/<agente>[-n].md` con elementos que el propietario acepta o rechaza.

    - [propuesta:: p1] [tipo:: prioridad] [tarea:: t-abc234] [prioridad:: alta] [estado:: pendiente] motivo
    - [propuesta:: p2] [tipo:: nueva_tarea] [titulo:: …] [esfuerzo:: 1h] [vence:: 2026-11-20] [prioridad:: media] [estado:: pendiente] motivo

Las líneas no tienen casilla, de modo que nunca se leen como tareas. Todo texto libre se reduce
a una línea inerte sin corchetes (SEC-10): un motivo no puede inyectar campos ni estados.
"""  # noqa: E501

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from lifeos.domain.areas import PROPOSALS_ROOT, SPECIALIST_AREAS
from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import clean
from lifeos.domain.task import Area, Effort, Priority
from lifeos.domain.weeks import IsoWeek

MAX_ITEMS = 20
MAX_REASON_CHARS = 300
MAX_TITLE_CHARS = 160
_KEY_RE = re.compile(r"p[0-9]{1,3}")
_NOTE_RE = re.compile(r"[a-z0-9-]{1,32}(-[0-9]{1,3})?")
_ITEM_LINE_RE = re.compile(r"\s*- \[propuesta::")
_FIELD_RE = re.compile(r"\[([a-z_]+)::\s*([^\[\]]*?)\s*\]")
_ALLOWED_FIELDS = frozenset(
    {"propuesta", "tipo", "tarea", "titulo", "esfuerzo", "vence", "prioridad", "estado"}
)
SOURCES = frozenset({"heuristica", "ia"})
_REF_PARTS = 3


class ProposalKind(StrEnum):
    PRIORIDAD = "prioridad"  # cambiar la prioridad de una tarea existente del área
    NUEVA_TAREA = "nueva_tarea"  # crear una tarea en el área


class ProposalStatus(StrEnum):
    PENDIENTE = "pendiente"
    ACEPTADA = "aceptada"
    RECHAZADA = "rechazada"


@dataclass(frozen=True, slots=True)
class ProposalItem:
    key: str
    kind: ProposalKind
    priority: Priority
    reason: str
    task_id: TaskId | None = None
    title: str | None = None
    effort: Effort | None = None
    due: date | None = None
    status: ProposalStatus = ProposalStatus.PENDIENTE

    def __post_init__(self) -> None:
        if not _KEY_RE.fullmatch(self.key):
            raise ValidationError("propuesta", self.key, "clave esperada p<n>")
        reason = clean(self.reason, MAX_REASON_CHARS)
        if not reason:
            raise ValidationError("motivo", "", "no puede estar vacío")
        object.__setattr__(self, "reason", reason)
        if self.title is not None:
            object.__setattr__(self, "title", clean(self.title, MAX_TITLE_CHARS) or None)
        if self.kind is ProposalKind.PRIORIDAD:
            self._check_priority_change()
        else:
            self._check_new_task()

    def _check_priority_change(self) -> None:
        if self.task_id is None:
            raise ValidationError("tarea", None, "un cambio de prioridad necesita la tarea")
        if self.title is not None or self.effort is not None or self.due is not None:
            raise ValidationError("título", self.title, "solo en una tarea nueva")

    def _check_new_task(self) -> None:
        if self.task_id is not None:
            raise ValidationError("tarea", str(self.task_id), "una tarea nueva no tiene id aún")
        if self.title is None:
            raise ValidationError("título", "", "una tarea nueva necesita título")
        if self.effort is None:
            raise ValidationError("esfuerzo", None, "una tarea nueva necesita esfuerzo")


@dataclass(frozen=True, slots=True)
class AreaProposal:
    agent_id: str
    area: Area
    week: IsoWeek
    source: str  # heuristica | ia
    items: tuple[ProposalItem, ...]

    def __post_init__(self) -> None:
        if self.area not in SPECIALIST_AREAS:
            raise ValidationError("área", str(self.area), "sin especialistas (RULES E.12)")
        if self.source not in SOURCES:
            raise ValidationError("fuente", self.source, "heuristica o ia")
        if len(self.items) > MAX_ITEMS:
            raise ValidationError("propuestas", len(self.items), f"máximo {MAX_ITEMS}")
        keys = [item.key for item in self.items]
        if len(set(keys)) != len(keys):
            raise ValidationError("propuestas", keys, "clave repetida")


def proposal_note_path(week: IsoWeek, agent_id: str, revision: int) -> str:
    suffix = "" if revision == 1 else f"-{revision}"
    return f"{PROPOSALS_ROOT}/{week}/{agent_id}{suffix}.md"


@dataclass(frozen=True, slots=True)
class ProposalRef:
    """Referencia estable a un elemento: `<semana>/<nota>/<clave>`."""

    week: IsoWeek
    note: str
    key: str

    @classmethod
    def parse(cls, raw: str) -> "ProposalRef":
        parts = raw.strip().split("/")
        if len(parts) != _REF_PARTS:
            raise ValidationError("propuesta", raw, "formato esperado AAAA-Wnn/<nota>/p<n>")
        week, note, key = parts
        if not _NOTE_RE.fullmatch(note) or not _KEY_RE.fullmatch(key):
            raise ValidationError("propuesta", raw, "formato esperado AAAA-Wnn/<nota>/p<n>")
        return cls(IsoWeek.parse(week), note, key)

    @property
    def path(self) -> str:
        return f"{PROPOSALS_ROOT}/{self.week}/{self.note}.md"

    def __str__(self) -> str:
        return f"{self.week}/{self.note}/{self.key}"


# --- Nota ---------------------------------------------------------------------------------------


def _render_item(item: ProposalItem) -> str:
    parts = [f"- [propuesta:: {item.key}]", f"[tipo:: {item.kind}]"]
    if item.task_id is not None:
        parts.append(f"[tarea:: {item.task_id}]")
    if item.title is not None:
        parts.append(f"[titulo:: {item.title}]")
    if item.effort is not None:
        parts.append(f"[esfuerzo:: {item.effort.render()}]")
    if item.due is not None:
        parts.append(f"[vence:: {item.due.isoformat()}]")
    parts += [f"[prioridad:: {item.priority}]", f"[estado:: {item.status}]", item.reason]
    return " ".join(parts)


def render_proposal(proposal: AreaProposal) -> str:
    lines = [
        f"# Propuestas de {proposal.agent_id} — {proposal.week}",
        "",
        f"[area:: {proposal.area}] [fuente:: {proposal.source}]",
        "",
        "> Estas propuestas no cambian nada hasta que las aceptes (`lifeos propuestas` o la web).",
        "",
        *(_render_item(item) for item in proposal.items),
    ]
    return "\n".join(lines) + "\n"


def _fields(line: str) -> dict[str, str] | None:
    fields: dict[str, str] = {}
    for key, value in _FIELD_RE.findall(line):
        if key not in _ALLOWED_FIELDS or key in fields:
            return None
        fields[key] = value
    return fields


def _parse_item(line: str) -> ProposalItem | None:
    fields = _fields(line)
    if fields is None:
        return None
    reason = _FIELD_RE.sub(" ", line.strip()[2:])
    try:
        return ProposalItem(
            key=fields["propuesta"],
            kind=ProposalKind(fields["tipo"]),
            priority=Priority.parse(fields["prioridad"]),
            reason=reason,
            task_id=TaskId(fields["tarea"]) if "tarea" in fields else None,
            title=fields.get("titulo"),
            effort=Effort.parse(fields["esfuerzo"]) if "esfuerzo" in fields else None,
            due=date.fromisoformat(fields["vence"]) if "vence" in fields else None,
            status=ProposalStatus(fields["estado"]),
        )
    except (KeyError, ValueError, ValidationError):  # línea mal formada: no se adivina
        return None


def _item_lines(text: str) -> list[tuple[int, ProposalItem]]:
    found: list[tuple[int, ProposalItem]] = []
    seen: set[str] = set()
    for index, line in enumerate(text.splitlines()):
        if not _ITEM_LINE_RE.match(line):
            continue
        item = _parse_item(line)
        if item is not None and item.key not in seen and len(found) < MAX_ITEMS:
            seen.add(item.key)
            found.append((index, item))
    return found


def parse_proposal_note(text: str) -> tuple[ProposalItem, ...]:
    """Elementos válidos de la nota; las líneas mal formadas o repetidas se ignoran."""
    return tuple(item for _, item in _item_lines(text))


def with_status(text: str, key: str, status: ProposalStatus) -> str:
    """Marca un elemento pendiente como aceptado o rechazado, sin tocar el resto de la nota."""
    if status is ProposalStatus.PENDIENTE:
        raise ValidationError("estado", str(status), "solo aceptada o rechazada")
    for index, item in _item_lines(text):
        if item.key != key:
            continue
        if item.status is not ProposalStatus.PENDIENTE:
            raise ValidationError("propuesta", key, f"ya está {item.status}")
        lines = text.split("\n")
        lines[index] = _render_item(_with(item, status))
        return "\n".join(lines)
    raise ValidationError("propuesta", key, "no existe en la nota")


def _with(item: ProposalItem, status: ProposalStatus) -> ProposalItem:
    return ProposalItem(
        item.key,
        item.kind,
        item.priority,
        item.reason,
        task_id=item.task_id,
        title=item.title,
        effort=item.effort,
        due=item.due,
        status=status,
    )


_HEADER_AREA_RE = re.compile(r"^\[area:: ([a-z]+)\] \[fuente:: [a-z]+\]$")


def proposal_area(text: str) -> Area | None:
    """Área declarada en la cabecera que escribió el código; None si falta o no es válida."""
    for line in text.splitlines():
        match = _HEADER_AREA_RE.fullmatch(line.strip())
        if match is not None:
            try:
                area = Area(match[1])
            except ValueError:
                return None
            return area if area in SPECIALIST_AREAS else None
    return None
