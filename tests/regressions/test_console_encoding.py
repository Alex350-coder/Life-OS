"""Regresión de la prueba real de la Fase 7 (2026-11-16): consola de Windows en cp1252.

`lifeos-calendario publicar --confirmar` publicó en Google y luego cayó con `UnicodeEncodeError`
al escribir "→" en una consola cp1252. Toda orden de la CLI debe pasar su salida a UTF-8 con
reemplazo, como ya hacía `lifeos`.
"""

import contextlib
import io
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from lifeos.cli.agents import main as agents_main
from lifeos.cli.backup import main as backup_main
from lifeos.cli.calendar import main as calendar_main
from lifeos.cli.indicators import main as indicators_main
from lifeos.cli.main import main as lifeos_main
from lifeos.cli.output import console


def _cp1252() -> io.TextIOWrapper:
    return io.TextIOWrapper(io.BytesIO(), encoding="cp1252")


def test_console_switches_a_cp1252_stream_to_utf8() -> None:
    stream = console(_cp1252())
    print("Plan aprobado → calendario 'Life OS' · 11:00–12:00", file=stream)
    assert stream.encoding == "utf-8"


def test_console_leaves_streams_without_reconfigure_alone() -> None:
    stream = io.StringIO()
    assert console(stream) is stream


@pytest.mark.parametrize(
    ("main", "argv"),
    [
        (lifeos_main, ["tareas"]),
        (calendar_main, ["estado"]),
        (agents_main, ["lista"]),
        (backup_main, ["crear"]),
        (indicators_main, []),
    ],
    ids=["lifeos", "calendario", "agentes", "respaldo", "indicadores"],
)
def test_every_cli_writes_utf8_to_a_cp1252_console(
    main: Callable[..., int],
    argv: list[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out, err = _cp1252(), _cp1252()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    with contextlib.suppress(SystemExit):
        main(argv, environ={}, project_root=tmp_path)
    assert out.encoding == "utf-8"
    assert err.encoding == "utf-8"
