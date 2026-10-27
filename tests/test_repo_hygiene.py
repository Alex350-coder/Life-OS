"""SEC-01: los secretos y el entorno local nunca se rastrean en Git."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GIT = shutil.which("git")
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{16,}|nvapi-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,})")

pytestmark = [pytest.mark.security, pytest.mark.skipif(GIT is None, reason="git no disponible")]


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    assert GIT is not None
    return subprocess.run(  # noqa: S603 - argumentos fijos, sin shell
        [GIT, *args], cwd=ROOT, capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize(
    "name", [".env", ".env.local", "secrets/token.txt", "logs/app.log", "logs/usage-2026-10.jsonl"]
)
def test_sensitive_paths_are_git_ignored(name: str) -> None:
    assert _git("check-ignore", "-q", name).returncode == 0


def test_env_example_is_not_ignored() -> None:
    assert _git("check-ignore", "-q", ".env.example").returncode == 1


def test_env_example_has_no_values() -> None:
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assignments = [line for line in lines if line and not line.startswith("#")]
    assert assignments
    assert all(line.endswith("=") for line in assignments)


def test_tracked_files_contain_no_api_keys() -> None:
    tracked = _git("ls-files", "-z").stdout.split("\0")
    offenders = [
        name
        for name in tracked
        if name
        and (ROOT / name).is_file()
        and SECRET_RE.search((ROOT / name).read_text(encoding="utf-8", errors="ignore"))
    ]
    assert offenders == []
