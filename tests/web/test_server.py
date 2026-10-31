"""Entrada `lifeos-web` (ADR-0013): solo 127.0.0.1, token en la terminal y errores claros."""

import io
import re
import shutil
import socket
from pathlib import Path

import pytest

from lifeos.web.server import EXIT_OK, EXIT_USAGE, main

PROJECT = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.integration


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    shutil.copytree(PROJECT / "config", root / "config")
    return root


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _run(tmp_path: Path, *argv: str, **env: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    environ = {"LIFEOS_LOGS_DIR": str(tmp_path / "logs"), **env}
    code = main(
        list(argv), environ=environ, project_root=_root(tmp_path), out=out, err=err, serve=False
    )
    return code, out.getvalue(), err.getvalue()


def test_prints_a_single_use_loopback_url(tmp_path: Path) -> None:
    port = _free_port()
    code, out, _ = _run(tmp_path, LIFEOS_WEB_PORT=str(port))
    assert code == EXIT_OK
    match = re.search(rf"http://127\.0\.0\.1:{port}/auth\?token=([A-Za-z0-9_-]{{43,}})", out)
    assert match is not None
    assert not (tmp_path / "logs").exists() or match[1] not in "".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "logs").glob("*")
    )


@pytest.mark.parametrize("port", ["80", "70000", "abc", "-1"])
def test_invalid_port_is_a_configuration_error(tmp_path: Path, port: str) -> None:
    code, out, err = _run(tmp_path, LIFEOS_WEB_PORT=port)
    assert code == EXIT_USAGE and "LIFEOS_WEB_PORT" in err and "token" not in out


def test_busy_port_fails_before_showing_a_token(tmp_path: Path) -> None:
    with socket.socket() as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen()
        port = busy.getsockname()[1]
        code, out, err = _run(tmp_path, LIFEOS_WEB_PORT=str(port))
    assert code == EXIT_USAGE and "token=" not in out and "no se pudo abrir" in err


def test_arguments_are_rejected(tmp_path: Path) -> None:
    code, _, err = _run(tmp_path, "--host", "0.0.0.0")  # noqa: S104
    assert code == EXIT_USAGE and "no acepta argumentos" in err


def test_invalid_configuration_stops_the_start(tmp_path: Path) -> None:
    root = _root(tmp_path)
    (root / "config" / "permissions.yaml").write_text(
        "!!python/object:os.system x\n", encoding="utf-8"
    )
    out, err = io.StringIO(), io.StringIO()
    code = main([], environ={}, project_root=root, out=out, err=err, serve=False)
    assert code == EXIT_USAGE and "Error de configuración" in err.getvalue()
