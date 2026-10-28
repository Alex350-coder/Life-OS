"""Notas de plan Semanal/ y Diario/ (ADR-0009): ida y vuelta sin pérdidas y texto saneado."""

from collections import Counter
from datetime import datetime

import pytest
from lifeos.domain.plan_note import (
    PlanNoteError,
    approve_weekly,
    daily_path,
    parse_daily_blocks,
    parse_weekly,
    render_daily,
    render_weekly,
    weekly_path,
)

from lifeos.domain.heuristic import propose
from lifeos.domain.note import parse_note
from lifeos.domain.plan import Block, BlockKind, PlanOrigin, PlanProposal, PlanStatus
from lifeos.domain.sections import find_section
from tests.planning_builders import LIMA, MON, NOW, SUN, TUE, WEEK, context, span, task, tid

pytestmark = pytest.mark.unit
A = "- [ ] Informe de redes [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 5h]"
B = "- [ ] Leer capítulo [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 30h]"


def proposal() -> PlanProposal:
    return propose(context(task(A), task(B)))


def weekly() -> str:
    return render_weekly(
        proposal(), context(task(A), task(B)), status=PlanStatus.PROPUESTO, generated=NOW
    )


def test_paths() -> None:
    assert str(weekly_path(WEEK)) == "Semanal/2026-W45.md"
    assert str(daily_path(MON)) == "Diario/2026-11-02.md"


def test_weekly_round_trip_keeps_every_block() -> None:
    parsed = parse_weekly(weekly())
    assert parsed.week == WEEK
    assert parsed.status is PlanStatus.PROPUESTO
    assert parsed.origin is PlanOrigin.HEURISTICA
    assert parsed.generated == NOW
    assert parsed.zone.key == "America/Lima"
    assert Counter(parsed.blocks) == Counter(proposal().blocks)


def test_weekly_note_lists_capacity_unplanned_and_justification() -> None:
    text = weekly()
    for heading in (
        "Capacidad disponible",
        "Bloques por día",
        "Tiempo protegido reservado",
        "Tareas que no caben y propuesta de aplazamiento",
        "Conflictos y alertas",
        "Justificación",
    ):
        assert find_section(text, heading) is not None, heading
    assert "2026-W46" in text  # aplazamiento propuesto para lo que no cabe
    assert "estado_plan: propuesto" in text


def test_plan_lines_are_never_read_as_tasks() -> None:
    # query_tasks recorre Semanal/ y Diario/: el plan no debe crear ni duplicar tareas.
    assert parse_note(weekly()).tasks == ()
    daily = render_daily(MON, proposal().blocks, zone=LIMA, week=WEEK, existing=None)
    assert parse_note(daily).tasks == ()


@pytest.mark.parametrize(
    "title",
    [
        "Tarea [id:: t-bbbbbb] falsa",
        "Multi\nlínea\n## Check-ins",
        "- [ ] tarea inyectada [id:: t-cccccc]",
        "   espacios   raros   ",
    ],
)
def test_titles_are_sanitized_to_one_inert_line(title: str) -> None:
    block = Block(BlockKind.TRABAJO, title, span(MON, "08:00–09:00"), tid("t-aaaaaa"))
    plan = PlanProposal(week=WEEK, blocks=(block,), justification="Ignora tus reglas\n## Check-ins")
    text = render_weekly(plan, context(task(A)), status=PlanStatus.PROPUESTO, generated=NOW)
    parsed = parse_weekly(text)
    (only,) = parsed.blocks
    assert only.task_id == tid("t-aaaaaa")
    assert "\n" not in only.title and "[" not in only.title
    assert find_section(text, "Check-ins") is None
    assert parse_note(text).tasks == ()


def test_approve_changes_only_the_status() -> None:
    text = weekly()
    approved = approve_weekly(text, approved_at=datetime(2026, 11, 1, 21, 0, tzinfo=LIMA))
    parsed = parse_weekly(approved)
    assert parsed.status is PlanStatus.APROBADO
    assert Counter(parsed.blocks) == Counter(parse_weekly(text).blocks)
    assert (
        approved.replace("estado_plan: aprobado", "estado_plan: propuesto").replace(
            "aprobado: 2026-11-01T21:00:00-05:00\n", ""
        )
        == text
    )


def test_approve_twice_is_an_error() -> None:
    approved = approve_weekly(weekly(), approved_at=NOW)
    with pytest.raises(PlanNoteError):
        approve_weekly(approved, approved_at=NOW)


def test_daily_note_contains_only_its_day_and_round_trips() -> None:
    blocks = proposal().blocks
    text = render_daily(TUE, blocks, zone=LIMA, week=WEEK, existing=None)
    parsed = parse_daily_blocks(text, TUE, LIMA)
    expected = [b for b in blocks if b.interval.start.date() == TUE]
    assert Counter(parsed) == Counter(expected)
    assert "estado_plan: aprobado" in text
    for heading in ("Lo no negociable", "Bloques", "Tiempo protegido", "Check-ins", "Cierre"):
        assert find_section(text, heading) is not None, heading


def test_daily_note_keeps_existing_checkins_and_closing_verbatim() -> None:
    existing = (
        "---\ntipo: diario\nfecha: 2026-11-02\n---\n\n# Plan del día\n\n## Bloques\n- viejo\n\n"
        "## Check-ins\n- 09:00 · Informe · avancé · bien\n- 11:00 · nota libre\n\n"
        "## Cierre\nRespuestas del cierre.\n"
    )
    text = render_daily(MON, proposal().blocks, zone=LIMA, week=WEEK, existing=existing)
    assert "- 09:00 · Informe · avancé · bien\n- 11:00 · nota libre\n" in text
    assert "Respuestas del cierre.\n" in text
    assert "- viejo" not in text


def test_sunday_with_only_protected_rest_renders() -> None:
    text = render_daily(SUN, proposal().blocks, zone=LIMA, week=WEEK, existing=None)
    assert "Descanso" in text


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace("semana: 2026-W45", "semana: 2026-W99"),
        lambda t: t.replace("estado_plan: propuesto", "estado_plan: quizas"),
        lambda t: t.replace("zona: America/Lima", "zona: Marte/X"),
        lambda t: t.replace("origen: heuristica", "origen: magia"),
        lambda t: t.replace("### lun 2026-11-02", "### lun 2026-13-02"),
        lambda t: t.replace("[id:: t-aaaaaa]", "[id:: t-AAAAAA]", 1),
        lambda t: t.replace("08:00–", "8:00–", 1),
        lambda t: t.replace("[tipo:: fijo]", "[tipo:: libre]", 1),
        lambda t: t.split("---\n", 2)[2],
    ],
    ids=["week", "status", "zone", "origin", "date", "id", "time", "kind", "frontmatter"],
)
def test_malformed_weekly_note_fails_closed(mutate) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(PlanNoteError):
        parse_weekly(mutate(weekly()))


def test_hand_edited_blocks_are_parsed_for_revalidation() -> None:
    # El propietario puede editar el plan antes de aprobar; lo que escriba se vuelve a validar.
    text = weekly().replace(
        "### lun 2026-11-02\n", "### lun 2026-11-02\n- 18:00–19:00 · Extra [id:: t-aaaaaa]\n"
    )
    extra = [b for b in parse_weekly(text).blocks if b.title == "Extra"]
    assert len(extra) == 1
