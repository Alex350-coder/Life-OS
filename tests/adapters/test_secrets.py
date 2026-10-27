"""Lectura estricta de `.env` y envoltorio `Secret` que nunca se imprime (SEC-08)."""

from pathlib import Path

import pytest
from lifeos.adapters.secrets import MissingSecretError, Secret, read_dotenv, resolve_secret

KEY = "nvapi-TESTKEY-0123456789abcdefXYZ"

pytestmark = [pytest.mark.unit, pytest.mark.security]


def test_secret_never_reveals_itself_by_accident() -> None:
    secret = Secret(KEY)
    assert KEY not in str(secret)
    assert KEY not in repr(secret)
    assert KEY not in f"{secret}"
    assert secret.reveal() == KEY


def test_secret_rejects_empty_or_multiline_values() -> None:
    for bad in ["", "   ", "a\nb", "a\rb"]:
        with pytest.raises(ValueError):
            Secret(bad)


def test_read_dotenv_parses_plain_and_quoted_values(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# comentario\n\nNVIDIA_API_KEY=abc\nLIFEOS_TIMEZONE=\"America/Lima\"\nVACIA=\nB='x y'\n",
        encoding="utf-8",
    )
    assert read_dotenv(env) == {
        "NVIDIA_API_KEY": "abc",
        "LIFEOS_TIMEZONE": "America/Lima",
        "VACIA": "",
        "B": "x y",
    }


def test_read_dotenv_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_dotenv(tmp_path / ".env") == {}


@pytest.mark.parametrize("line", ["sin_igual", "1CLAVE=x", "export A=b", "A=$(rm -rf)", 'A="x'])
def test_read_dotenv_rejects_malformed_lines_without_echoing_them(
    tmp_path: Path, line: str
) -> None:
    env = tmp_path / ".env"
    env.write_text(f"OK=1\n{line}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="línea 2") as info:
        read_dotenv(env)
    assert line not in str(info.value)


def test_resolve_secret_prefers_process_environment() -> None:
    secret = resolve_secret(
        "NVIDIA_API_KEY",
        environ={"NVIDIA_API_KEY": "de-entorno"},
        dotenv={"NVIDIA_API_KEY": "de-archivo"},
    )
    assert secret.reveal() == "de-entorno"


def test_resolve_secret_falls_back_to_dotenv_and_fails_clearly() -> None:
    assert resolve_secret("K", environ={}, dotenv={"K": "v"}).reveal() == "v"
    with pytest.raises(MissingSecretError, match="K"):
        resolve_secret("K", environ={"K": ""}, dotenv={})
