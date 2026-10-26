"""Análisis y edición mínima de líneas de tarea (spec formato-de-tareas, RULES §D3)."""

import dataclasses
from datetime import date

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.task import (
    Area,
    Checkbox,
    Effort,
    ExplicitState,
    Priority,
    Progress,
    TaskStatus,
)
from lifeos.domain.task_line import (
    TaskLine,
    parse_task_line,
    with_checkbox,
    with_field,
    with_id,
)

pytestmark = pytest.mark.unit

SPEC_EXAMPLE = (
    "- [ ] Preparar evaluación de redes [id:: t-7f3k2m] [area:: universidad] "
    "[esfuerzo:: 4h] [avance:: 25%] [prioridad:: alta] 📅 2026-10-30"
)


def _parse(line: str) -> TaskLine:
    """Analiza una línea que se sabe que es tarea (falla el test si no lo es)."""
    parsed = parse_task_line(line)
    assert parsed is not None
    return parsed


# --- Análisis: campos tipados -------------------------------------------------------------


def test_spec_example_parses_all_typed_fields() -> None:
    # Arrange / Act
    task = _parse(SPEC_EXAMPLE).task

    # Assert
    assert task.title == "Preparar evaluación de redes"
    assert task.checkbox is Checkbox.PENDING
    assert task.id == TaskId("t-7f3k2m")
    assert task.area is Area.UNIVERSIDAD
    assert task.effort == Effort(240)
    assert task.progress == Progress(25)
    assert task.priority is Priority.ALTA
    assert task.due == date(2026, 10, 30)
    assert task.scheduled is None
    assert task.recurrence is None
    assert task.state is None
    assert task.inferred is False
    assert task.missing_required_fields() == ()


def test_spec_example_has_no_extra_fields_and_keeps_raw() -> None:
    parsed = _parse(SPEC_EXAMPLE)

    assert parsed.extra_fields == ()
    assert parsed.raw == SPEC_EXAMPLE


def test_due_and_scheduled_emojis_are_distinct_dates() -> None:
    task = _parse("- [ ] Entregar informe 📅 2026-11-05 ⏳ 2026-11-02").task

    assert task.due == date(2026, 11, 5)
    assert task.scheduled == date(2026, 11, 2)
    assert task.title == "Entregar informe"
    assert task.status is TaskStatus.PROGRAMADA


def test_scheduled_only_does_not_set_due() -> None:
    task = _parse("- [ ] Estudiar ⏳ 2026-11-02").task

    assert task.due is None
    assert task.scheduled == date(2026, 11, 2)


def test_due_emoji_with_variation_selector_is_recognized() -> None:
    task = _parse("- [ ] Pagar luz 📅️ 2026-11-10").task

    assert task.due == date(2026, 11, 10)
    assert task.title == "Pagar luz"


@pytest.mark.parametrize(
    ("mark", "checkbox", "status"),
    [
        (" ", Checkbox.PENDING, TaskStatus.PROPUESTA),
        ("x", Checkbox.DONE, TaskStatus.COMPLETADA),
        ("X", Checkbox.DONE, TaskStatus.COMPLETADA),
        ("-", Checkbox.CANCELLED, TaskStatus.CANCELADA),
    ],
)
def test_checkbox_marks_map_to_checkbox_and_status(
    mark: str, checkbox: Checkbox, status: TaskStatus
) -> None:
    task = _parse(f"- [{mark}] Tarea").task

    assert task.checkbox is checkbox
    assert task.status is status


@pytest.mark.parametrize("mark", ["/", ">", "?", "!"])
def test_unknown_checkbox_mark_raises_casilla_error(mark: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_task_line(f"- [{mark}] Tarea rara")

    assert exc_info.value.field == "casilla"


def test_recurrence_is_text_after_emoji_up_to_next_token() -> None:
    task = _parse("- [ ] Regar plantas 🔁 every week 📅 2026-11-01 [area:: cotidiano]").task

    assert task.recurrence == "every week"
    assert task.due == date(2026, 11, 1)
    assert task.title == "Regar plantas"


def test_recurrence_at_end_of_line_is_stripped() -> None:
    task = _parse("- [ ] Pagar alquiler 🔁 every month on the 1st  ").task

    assert task.recurrence == "every month on the 1st"
    assert task.title == "Pagar alquiler"


def test_state_and_inferred_fields_are_typed() -> None:
    task = _parse("- [ ] Esperar respuesta [estado:: bloqueada] [inferido:: true]").task

    assert task.state is ExplicitState.BLOQUEADA
    assert task.inferred is True
    assert task.status is TaskStatus.BLOQUEADA


def test_inferred_false_is_parsed_as_false() -> None:
    task = _parse("- [ ] Confirmado [inferido:: false]").task

    assert task.inferred is False


def test_field_values_are_trimmed() -> None:
    task = _parse("- [ ] Correr [area::   salud  ] [esfuerzo::  30m ]").task

    assert task.area is Area.SALUD
    assert task.effort == Effort(30)


def test_missing_area_and_effort_are_allowed_at_parse_time() -> None:
    task = _parse("- [ ] Idea suelta").task

    assert task.area is None
    assert task.effort is None
    assert task.priority is Priority.MEDIA
    assert task.missing_required_fields() == ("area", "esfuerzo")


def test_obsidian_tasks_date_emojis_are_ignored_and_removed_from_title() -> None:
    # U+2795 (HEAVY PLUS SIGN) es el emoji real de "creada" en Obsidian Tasks, no un '+' ambiguo.
    line = "- [x] Lavar ropa ➕ 2026-10-01 🛫 2026-10-02 ✅ 2026-10-03 ❌ 2026-10-04"  # noqa: RUF001

    task = _parse(line).task

    assert task.title == "Lavar ropa"
    assert task.due is None
    assert task.scheduled is None


# --- Título ---------------------------------------------------------------------------------


def test_title_collapses_whitespace_left_by_removed_tokens() -> None:
    task = _parse("- [ ] Leer   [area:: hobbies]  capítulo  3   [esfuerzo:: 1h]").task

    assert task.title == "Leer capítulo 3"


def test_title_keeps_wikilinks_and_brackets_without_double_colon() -> None:
    task = _parse("- [ ] Revisar [[Apuntes de redes]] y [nota] [area:: universidad]").task

    assert task.title == "Revisar [[Apuntes de redes]] y [nota]"


def test_title_keeps_unicode_accents_and_unknown_emoji() -> None:
    task = _parse("- [ ] Comprar café ☕ y piñatas 🎉 [area:: cotidiano]").task

    assert task.title == "Comprar café ☕ y piñatas 🎉"


# --- Campos desconocidos --------------------------------------------------------------------


def test_unknown_fields_are_preserved_in_order_of_appearance() -> None:
    line = "- [ ] Tarea [contexto:: casa] [area:: salud] [energia:: baja] [contexto2:: x y]"

    parsed = _parse(line)

    assert parsed.extra_fields == (
        ("contexto", "casa"),
        ("energia", "baja"),
        ("contexto2", "x y"),
    )
    assert parsed.task.title == "Tarea"


# --- Líneas que no son tareas ---------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "Texto normal de una nota",
        "# Encabezado",
        "## [ ] Encabezado con corchetes",
        "- item sin casilla",
        "* otro item",
        "1. [ ] lista numerada",
        "-[ ] sin espacio tras la viñeta",
        "> - [ ] dentro de una cita",
    ],
)
def test_non_task_lines_return_none(line: str) -> None:
    assert parse_task_line(line) is None


# --- Valores inválidos ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("token", "field"),
    [
        ("[esfuerzo:: 0h]", "esfuerzo"),
        ("[esfuerzo:: -1h]", "esfuerzo"),
        ("[esfuerzo:: abc]", "esfuerzo"),
        ("[avance:: 150%]", "avance"),
        ("[area:: trabajo]", "area"),
        ("[id:: t-123]", "id"),
        ("📅 2026-02-30", "fecha límite"),
        ("⏳ 2026-02-30", "fecha programada"),
    ],
)
def test_invalid_values_raise_validation_error_naming_field(token: str, field: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        parse_task_line(f"- [ ] Tarea con error {token}")

    assert exc_info.value.field == field


def test_repeated_known_key_raises_duplicate_error() -> None:
    with pytest.raises(ValidationError, match="duplicado"):
        parse_task_line("- [ ] Tarea [area:: salud] [area:: cotidiano]")


# --- Ida y vuelta byte a byte ---------------------------------------------------------------


@pytest.mark.parametrize(
    "line",
    [
        SPEC_EXAMPLE,
        "\t\t- [ ] Subtarea con tabulaciones [area:: proyectos]",
        "    - [ ] Subtarea con espacios",
        "* [ ] Viñeta con asterisco",
        "+ [x] Viñeta con más",
        "- [X] Mayúscula conservada [esfuerzo:: 30m]",
        "- [-] Cancelada",
        "- [ ] Con campos raros [foo:: bar] [baz::qux]",
        "- [ ] Emoji desconocido 🚀 en el texto",
        "- [x] Hecha ✅ 2026-10-20",
        "- [ ] Espacios finales [area:: salud]   ",
        "- [ ] Ñandú, acción y café ☕ [area:: hobbies]",
        "- [ ] Variante 📅️ 2026-11-10",
        "- [ ] Recurrente 🔁 every day ⏳ 2026-10-27",
        "- [ ] Enlace [[Nota]]  con  espacios   dobles",
    ],
)
def test_render_returns_raw_line_byte_identical(line: str) -> None:
    assert _parse(line).render() == line


def test_task_line_is_frozen() -> None:
    parsed = _parse("- [ ] Tarea")

    with pytest.raises(dataclasses.FrozenInstanceError):
        parsed.raw = "otra"  # type: ignore[misc]


# --- with_id --------------------------------------------------------------------------------


def test_with_id_inserts_id_after_title_before_first_token() -> None:
    line = _parse("- [ ] Comprar pan [area:: cotidiano]")

    updated = with_id(line, TaskId("t-222222"))

    assert updated.render() == "- [ ] Comprar pan [id:: t-222222] [area:: cotidiano]"
    assert updated.task.id == TaskId("t-222222")


def test_with_id_appends_at_end_when_no_tokens() -> None:
    line = _parse("- [ ] Llamar a mamá")

    updated = with_id(line, TaskId("t-333333"))

    assert updated.render() == "- [ ] Llamar a mamá [id:: t-333333]"


def test_with_id_before_emoji_token_keeps_rest_unchanged() -> None:
    line = _parse("\t- [x] Entregar  tarea 📅 2026-10-30 [foo:: bar]  ")

    updated = with_id(line, TaskId("t-444444"))

    assert updated.render() == "\t- [x] Entregar  tarea [id:: t-444444] 📅 2026-10-30 [foo:: bar]  "
    assert updated.extra_fields == (("foo", "bar"),)


def test_with_id_does_not_mutate_original() -> None:
    original = _parse("- [ ] Comprar pan [area:: cotidiano]")

    with_id(original, TaskId("t-222222"))

    assert original.task.id is None
    assert original.render() == "- [ ] Comprar pan [area:: cotidiano]"


def test_with_id_on_task_that_already_has_id_raises() -> None:
    line = _parse(SPEC_EXAMPLE)

    with pytest.raises(ValidationError):
        with_id(line, TaskId("t-222222"))


# --- with_checkbox --------------------------------------------------------------------------


def test_with_checkbox_marks_done_changing_only_the_mark() -> None:
    line = _parse(SPEC_EXAMPLE)

    updated = with_checkbox(line, Checkbox.DONE)

    assert updated.render() == SPEC_EXAMPLE.replace("- [ ]", "- [x]", 1)
    assert updated.task.status is TaskStatus.COMPLETADA


def test_with_checkbox_cancels_indented_task() -> None:
    line = _parse("  * [ ] Sub [area:: salud]  ")

    updated = with_checkbox(line, Checkbox.CANCELLED)

    assert updated.render() == "  * [-] Sub [area:: salud]  "


def test_with_checkbox_reopens_done_task() -> None:
    line = _parse("- [X] Hecha")

    updated = with_checkbox(line, Checkbox.PENDING)

    assert updated.render() == "- [ ] Hecha"
    assert updated.task.checkbox is Checkbox.PENDING


def test_with_checkbox_does_not_mutate_original() -> None:
    original = _parse("- [ ] Tarea")

    with_checkbox(original, Checkbox.DONE)

    assert original.task.checkbox is Checkbox.PENDING
    assert original.render() == "- [ ] Tarea"


# --- with_field -----------------------------------------------------------------------------


def test_with_field_replaces_existing_value_in_place() -> None:
    line = _parse(SPEC_EXAMPLE)

    updated = with_field(line, "avance", "50%")

    assert updated.render() == SPEC_EXAMPLE.replace("[avance:: 25%]", "[avance:: 50%]")
    assert updated.task.progress == Progress(50)


def test_with_field_appends_missing_field_at_end() -> None:
    line = _parse("- [ ] Comprar pan 📅 2026-10-30")

    updated = with_field(line, "esfuerzo", "30m")

    assert updated.render() == "- [ ] Comprar pan 📅 2026-10-30 [esfuerzo:: 30m]"
    assert updated.task.effort == Effort(30)


def test_with_field_appends_before_trailing_whitespace() -> None:
    line = _parse("- [ ] Comprar pan   ")

    updated = with_field(line, "area", "cotidiano")

    assert updated.render() == "- [ ] Comprar pan [area:: cotidiano]   "


def test_with_field_replaces_unknown_field_value() -> None:
    line = _parse("- [ ] Tarea [contexto:: casa] [area:: salud]")

    updated = with_field(line, "contexto", "oficina")

    assert updated.render() == "- [ ] Tarea [contexto:: oficina] [area:: salud]"
    assert updated.extra_fields == (("contexto", "oficina"),)


def test_with_field_invalid_value_raises() -> None:
    line = _parse("- [ ] Tarea [esfuerzo:: 1h]")

    with pytest.raises(ValidationError) as exc_info:
        with_field(line, "esfuerzo", "0h")

    assert exc_info.value.field == "esfuerzo"


def test_with_field_does_not_mutate_original() -> None:
    original = _parse(SPEC_EXAMPLE)

    with_field(original, "prioridad", "baja")

    assert original.task.priority is Priority.ALTA
    assert original.render() == SPEC_EXAMPLE
