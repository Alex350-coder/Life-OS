"""Arnés de la web (Fase 5): la aplicación real sobre una copia de `config/` y una bóveda
temporal con datos inventados. Nunca usa la bóveda ni los logs reales del propietario."""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lifeos.bootstrap import Settings
from lifeos.web.app import create_app
from lifeos.web.security import LaunchToken
from tests.daily_builders import TASKS
from tests.fakes import FakeClock
from tests.planning_builders import NOW
from tests.planning_samples import HORARIO

PROJECT = Path(__file__).resolve().parent.parent.parent
PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = "token-de-arranque-de-prueba-" + "x" * 20  # inventado; nunca es un secreto real


@dataclass
class Monotonic:
    """Reloj monotónico controlado para la caducidad del token, las sesiones y la tasa."""

    value: float = 1000.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


@dataclass
class Web:
    root: Path
    vault: Path
    clock: FakeClock
    monotonic: Monotonic
    client: TestClient

    def login(self) -> str:
        """Abre una sesión con el token de arranque y devuelve el token CSRF."""
        response = self.client.get("/auth", params={"token": TOKEN}, follow_redirects=False)
        assert response.status_code == 303, response.text
        session = self.client.get("/api/session")
        assert session.status_code == 200, session.text
        csrf = session.json()["data"]["csrf_token"]
        assert isinstance(csrf, str)
        return csrf

    def post(self, path: str, body: object, csrf: str) -> "object":
        return self.client.post(path, json=body, headers={"X-CSRF-Token": csrf, "Origin": ORIGIN})

    def note(self, relative: str) -> str:
        return (self.vault / relative).read_text(encoding="utf-8")

    def write(self, relative: str, text: str) -> None:
        path = self.vault / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def build_web(tmp_path: Path, *, now: datetime = NOW, dist: Path | None = None) -> Web:
    root = tmp_path / "project"
    shutil.copytree(PROJECT / "config", root / "config")
    vault = tmp_path / "vault"
    (vault / "_sistema").mkdir(parents=True)
    (vault / "_sistema" / "horario.md").write_text(HORARIO, encoding="utf-8")
    (vault / "01-Universidad").mkdir()
    (vault / "01-Universidad" / "redes.md").write_text(TASKS, encoding="utf-8")
    settings = Settings.from_env(
        root, {"LIFEOS_VAULT_PATH": str(vault), "LIFEOS_LOGS_DIR": str(root / "logs")}
    )
    clock = FakeClock(now)
    monotonic = Monotonic()
    app = create_app(
        settings,
        port=PORT,
        clock=clock,
        monotonic=monotonic,
        launch_token=LaunchToken(monotonic, value=TOKEN),
        dist_dir=dist or tmp_path / "sin-dist",
    )
    client = TestClient(app, base_url=ORIGIN)
    return Web(root, vault, clock, monotonic, client)


@pytest.fixture
def web(tmp_path: Path) -> Iterator[Web]:
    harness = build_web(tmp_path)
    with harness.client:
        yield harness
