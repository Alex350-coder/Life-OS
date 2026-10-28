"""Constructores de contexto y bloques para las pruebas del planificador (datos inventados)."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from lifeos.domain.capacity import Interval, local_datetime
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import Block, BlockKind, PlanningContext, build_context
from lifeos.domain.schedule import TimeRange
from lifeos.domain.task import Task
from lifeos.domain.task_line import parse_task_line
from lifeos.domain.weeks import IsoWeek
from tests.planning_samples import HORARIO

LIMA = ZoneInfo("America/Lima")
WEEK = IsoWeek(2026, 45)  # lunes 2026-11-02 … domingo 2026-11-08
MON, TUE, WED, THU, FRI, SAT, SUN = WEEK.days()
# Domingo anterior por la noche: toda la semana 45 está en el futuro.
NOW = datetime(2026, 11, 1, 20, 0, tzinfo=LIMA)


def task(line: str) -> Task:
    parsed = parse_task_line(line)
    assert parsed is not None
    return parsed.task


def tid(value: str) -> TaskId:
    return TaskId(value)


def context(
    *tasks: Task, horario: str = HORARIO, now: datetime = NOW, week: IsoWeek = WEEK
) -> PlanningContext:
    return build_context(parse_horario(horario), week, tasks, now=now)


def span(day: date, text: str, zone: ZoneInfo = LIMA) -> Interval:
    rng = TimeRange.parse(text)
    return Interval(local_datetime(day, rng.start, zone), local_datetime(day, rng.end, zone))


def work(day: date, text: str, task_id: str, title: str = "Trabajo") -> Block:
    return Block(BlockKind.TRABAJO, title, span(day, text), TaskId(task_id))


def at(day: date, hhmm: str) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime.combine(day, time(int(hours), int(minutes)), tzinfo=LIMA)
