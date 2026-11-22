"""Formato y color de la salida de la CLI (pedido del propietario, 2026-11-16).

Los presentadores siguen devolviendo texto plano. Este módulo decora cada línea ya terminada
cuando la salida es una terminal real: errores en rojo, avisos en amarillo, éxitos en verde,
secciones en negrita y separadas, viñetas, y referencias, ids, estados, prioridades y órdenes
resaltados. Sin terminal, con `NO_COLOR` o si la consola no acepta ANSI, no cambia nada.
"""

import io
import os
import re
import sys
from collections.abc import Callable, Mapping
from typing import TextIO

RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"
BOLD_RED = "\x1b[1;31m"
BOLD_YELLOW = "\x1b[1;33m"
BOLD_CYAN = "\x1b[1;36m"

ERROR_MARK = "✖"
WARNING_MARK = "⚠"
SUCCESS_MARK = "✔"
BULLET = "•"

_WARNING_PREFIXES = ("Aviso", "Alertas", "No caben", "(El modelo no respondió")
_SUCCESS = re.compile(
    r"\b(aprobad[oa]|activad?[oa]|activo|probad[oa]|aceptad[oa]|rechazad[oa]|guardad[oa]s?"
    r"|escrit[oa]s?|aplicad[oa]|publicad[oa]s?|registrad[oa]s?|restaurad[oa]|desactivad[oa]"
    r"|conectad[oa]|cread[oa]s?|completad[oa])\b",
    re.IGNORECASE,
)
_NEGATIVE = re.compile(r"^(No |Sin )|\b(sin aprobar|no se)\b", re.IGNORECASE)
_LIST_ITEM = re.compile(r"^(\s*)- (.*)$")
# Una fila de datos (tarea o propuesta), no un título: se resalta, no va en negrita.
_ROW_START = re.compile(r"^(t-[a-z0-9]{6}|\d{4}-W\d{2}/)")

# Una sola pasada por línea: el primer grupo que coincide gana y no se solapan.
_STATE_COLORS = {
    "activo": GREEN,
    "aprobado": CYAN,
    "probado": CYAN,
    "borrador": YELLOW,
    "desactivado": RED,
    "pendiente": YELLOW,
    "aceptada": GREEN,
    "rechazada": RED,
    "alta": RED,
    "media": YELLOW,
    "baja": DIM,
}
_TOKENS = re.compile(
    r"(?P<sep> · )"
    r"|(?P<ref>\b\d{4}-W\d{2}/[\w-]+/p\d+\b)"
    r"|(?P<task>\bt-[a-z0-9]{6}\b)"
    r"|(?P<cmd>'(?:lifeos|uv run)[^']*')"
    r"|(?P<path>(?<![\w/])[\w./-]+/[\w./ -]*?\.md\b)"
    r"|(?<= · )(?P<word>" + "|".join(_STATE_COLORS) + r")(?= · |$|\.)"
)


def _paint(text: str, color: str) -> str:
    return f"{color}{text}{RESET}"


def _token(match: re.Match[str]) -> str:
    kind, text = match.lastgroup, match.group(0)
    if kind == "sep":
        return _paint(text, DIM)
    if kind in ("ref", "task"):
        return _paint(text, MAGENTA)
    if kind == "cmd":
        return _paint(text, CYAN)
    if kind == "path":
        return _paint(text, BLUE)
    return _paint(text, _STATE_COLORS[text])


def _highlight(text: str) -> str:
    return _TOKENS.sub(_token, text)


def _is_heading(line: str) -> bool:
    return not line.startswith((" ", "-")) and line.endswith(":")


def style_line(line: str, *, error: bool, first: bool) -> str:
    """Decora una línea de salida ya terminada (sin el salto final)."""
    if not line.strip():
        return line
    if error:
        head, sep, rest = line.partition(": ")
        return f"{_paint(f'{ERROR_MARK} {head}{sep}', BOLD_RED)}{rest}"
    if line.startswith("! "):
        return f"{_paint(WARNING_MARK, BOLD_YELLOW)} {_paint(line[2:], YELLOW)}"
    if line.startswith(_WARNING_PREFIXES):
        return f"{_paint(WARNING_MARK, BOLD_YELLOW)} {_paint(line, YELLOW)}"
    if _is_heading(line):
        heading = _paint(line, BOLD_CYAN)
        return heading if first else f"\n{heading}"
    item = _LIST_ITEM.match(line)
    if item:
        indent, rest = item.groups()
        return f"{indent}{_paint(BULLET, DIM)} {_highlight(rest)}"
    if not line.startswith(" ") and _SUCCESS.search(line) and not _NEGATIVE.search(line):
        return f"{_paint(SUCCESS_MARK, GREEN)} {_highlight(line)}"
    if first and not line.startswith(" ") and not _ROW_START.match(line):
        return _paint(line, BOLD)
    return _highlight(line)


class StyledStream(io.TextIOBase):
    """Envoltorio de la consola que decora cada línea completa al escribirla."""

    def __init__(self, target: TextIO, *, error: bool) -> None:
        super().__init__()
        self._target = target
        self._error = error
        self._pending = ""
        self._first = True

    @property
    def encoding(self) -> str:  # type: ignore[override]
        return self._target.encoding

    def isatty(self) -> bool:
        return self._target.isatty()

    def writable(self) -> bool:
        return True

    def _emit(self, line: str) -> str:
        styled = style_line(line, error=self._error, first=self._first)
        if line.strip():
            self._first = False
        return styled

    def write(self, text: str) -> int:
        self._pending += text
        *lines, self._pending = self._pending.split("\n")
        if lines:
            self._target.write("".join(f"{self._emit(line)}\n" for line in lines))
        return len(text)

    def flush(self) -> None:
        if self._pending:
            self._target.write(self._emit(self._pending))
            self._pending = ""
        self._target.flush()


def enable_virtual_terminal(stream: TextIO) -> bool:
    """En Windows activa las secuencias ANSI en la consola; en otros sistemas ya funcionan."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(stream.fileno())
        kernel32 = ctypes.windll.kernel32
        mode = ctypes.c_uint32()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        enable_vt = 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode.value | enable_vt))
    except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
        return False


def color_enabled(
    stream: TextIO,
    environ: Mapping[str, str],
    *,
    enable_vt: Callable[[TextIO], bool] = enable_virtual_terminal,
) -> bool:
    """`NO_COLOR` lo apaga siempre; `FORCE_COLOR` lo enciende aunque no haya terminal."""
    if environ.get("NO_COLOR"):
        return False
    if environ.get("FORCE_COLOR"):
        return True
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty()) and enable_vt(stream)


def styled(stream: TextIO, *, error: bool = False) -> TextIO:
    """La consola decorada si procede; si no, la misma consola sin cambios."""
    if color_enabled(stream, os.environ, enable_vt=lambda s: enable_virtual_terminal(s)):
        return StyledStream(stream, error=error)  # type: ignore[return-value]
    return stream
