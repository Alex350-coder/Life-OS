"""Salida de la CLI con formato y color (pedido del propietario, 2026-11-16).

El estilo solo se aplica en una terminal real. Redirigida a un archivo, con NO_COLOR o en las
pruebas, la salida queda en texto plano idéntico al de antes.
"""

import io
import re

import pytest

from lifeos.cli.output import console
from lifeos.cli.style import StyledStream, color_enabled, style_line

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return ANSI.sub("", text)


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


# --- Cuándo hay color ---------------------------------------------------------------------------


def test_no_color_outside_a_terminal() -> None:
    assert not color_enabled(io.StringIO(), {}, enable_vt=lambda _s: True)


def test_color_in_a_terminal() -> None:
    assert color_enabled(FakeTty(), {}, enable_vt=lambda _s: True)


def test_no_color_env_wins_even_in_a_terminal() -> None:
    assert not color_enabled(FakeTty(), {"NO_COLOR": "1"}, enable_vt=lambda _s: True)


def test_force_color_works_when_redirected() -> None:
    assert color_enabled(io.StringIO(), {"FORCE_COLOR": "1"}, enable_vt=lambda _s: True)


def test_no_color_when_the_console_rejects_ansi() -> None:
    assert not color_enabled(FakeTty(), {}, enable_vt=lambda _s: False)


def test_console_keeps_plain_streams_untouched() -> None:
    stream = io.StringIO()
    assert console(stream) is stream


def test_console_styles_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setattr("lifeos.cli.style.enable_virtual_terminal", lambda _s: True)
    assert isinstance(console(FakeTty()), StyledStream)


# --- Formato de cada línea ----------------------------------------------------------------------


def test_errors_are_red_with_a_cross() -> None:
    line = style_line("Error: universidad está en estado borrador", error=True, first=True)
    assert plain(line) == "✖ Error: universidad está en estado borrador"
    assert "\x1b[31" in line or "\x1b[1;31" in line


def test_warnings_are_yellow_with_a_sign() -> None:
    for raw, shown in [
        ("! Nota omitida: Propuestas/x.md", "⚠ Nota omitida: Propuestas/x.md"),
        (
            "Aviso: la nota de hoy no sigue el formato",
            "⚠ Aviso: la nota de hoy no sigue el formato",
        ),
    ]:
        line = style_line(raw, error=False, first=False)
        assert plain(line) == shown
        assert "\x1b[33" in line


def test_section_headings_are_bold_and_separated_by_a_blank_line() -> None:
    line = style_line("Importados:", error=False, first=False)
    assert plain(line) == "\nImportados:"
    assert "\x1b[1" in line


def test_first_heading_gets_no_blank_line() -> None:
    assert plain(
        style_line("Núcleo (config/agents, siempre activos):", error=False, first=True)
    ) == ("Núcleo (config/agents, siempre activos):")


def test_success_lines_get_a_green_check() -> None:
    for raw in [
        "universidad: v1 aprobado.",
        "Aceptada 2026-W47/universidad/p1 (tarea t-s5c93j).",
        "Propuestas escritas en Propuestas/2026-W47/universidad.md. Revísalas.",
    ]:
        line = style_line(raw, error=False, first=False)
        assert plain(line) == f"✔ {raw}"
        assert "\x1b[32" in line


def test_negative_lines_are_not_marked_as_success() -> None:
    for raw in [
        "No se activa: pruébalo con 'lifeos-agentes probar'.",
        "Hoy 2026-11-16 · plan sin aprobar",
    ]:
        assert not plain(style_line(raw, error=False, first=False)).startswith("✔")


def test_list_items_use_a_bullet_and_keep_their_indent() -> None:
    assert plain(style_line("- coach · núcleo · activo", error=False, first=False)) == (
        "• coach · núcleo · activo"
    )
    assert plain(style_line("  - Mecanografía · 30 min", error=False, first=False)) == (
        "  • Mecanografía · 30 min"
    )


def test_ids_states_and_priorities_are_highlighted() -> None:
    line = style_line(
        "- 2026-W47/universidad/p1 · nueva tarea · t-abc234 · alta · activo",
        error=False,
        first=False,
    )
    assert plain(line) == "• 2026-W47/universidad/p1 · nueva tarea · t-abc234 · alta · activo"
    assert "\x1b[35m2026-W47/universidad/p1" in line  # referencia en magenta
    assert "\x1b[35mt-abc234" in line  # id de tarea en magenta
    assert "\x1b[31malta" in line  # prioridad alta en rojo
    assert "\x1b[32mactivo" in line  # estado activo en verde


def test_commands_in_quotes_are_cyan() -> None:
    line = style_line("Siguiente: 'lifeos-agentes aprobar'.", error=False, first=False)
    assert "\x1b[36m'lifeos-agentes aprobar'" in line


def test_blank_lines_stay_blank() -> None:
    assert style_line("", error=False, first=False) == ""


# --- El flujo ------------------------------------------------------------------------------------


def test_styled_stream_styles_whole_lines_written_in_pieces() -> None:
    target = io.StringIO()
    stream = StyledStream(target, error=False)
    print("Importados:", file=stream)
    stream.write("- universidad · v1 ")
    stream.write("· activo\n")
    stream.flush()
    assert plain(target.getvalue()) == "Importados:\n• universidad · v1 · activo\n"


def test_styled_stream_flushes_a_trailing_partial_line() -> None:
    target = io.StringIO()
    stream = StyledStream(target, error=True)
    stream.write("Error: sin salto")
    stream.flush()
    assert plain(target.getvalue()) == "✖ Error: sin salto"


def test_styled_stream_reports_the_target_encoding() -> None:
    target = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
    assert StyledStream(target, error=False).encoding == "utf-8"


def test_a_task_row_first_is_highlighted_not_bolded() -> None:
    row = "t-yskya8 · propuesta · universidad · 1h · Siguiente paso (01-Universidad/tareas.md)"
    line = style_line(row, error=False, first=True)
    assert plain(line) == row
    assert line.startswith("[35mt-yskya8")


def test_virtual_terminal_is_refused_for_a_stream_without_a_console() -> None:
    from lifeos.cli.style import enable_virtual_terminal

    assert enable_virtual_terminal(io.StringIO()) is False
