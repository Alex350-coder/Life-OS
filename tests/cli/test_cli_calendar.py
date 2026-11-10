"""`lifeos-calendario` de punta a punta contra un Google falso (F7-T01…T03, SEC-13).

Recorre el ciclo completo: conectar cada permiso por separado, leer la disponibilidad, proponer
y aprobar un plan que la respeta, publicar solo tras confirmar y sin duplicados, y revocar
(desde Life OS y desde Google). Bóveda y credenciales inventadas.
"""

import io
import json
import shutil
import threading
import urllib.request
from pathlib import Path

import pytest

from lifeos.cli.calendar import main as calendar_main
from lifeos.cli.main import main as lifeos_main
from tests.fake_google import READ, WRITE, FakeGoogle, fake_google  # noqa: F401 - fixture
from tests.fakes import FakeClock
from tests.planning_builders import NOW
from tests.planning_samples import HORARIO

PROJECT = Path(__file__).resolve().parent.parent.parent
CLIENT_ID = "123-prueba.apps.googleusercontent.com"
TASKS = (
    "- [ ] Informe de redes [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 2h] 📅 2026-11-04\n"
)
WEEK = "2026-W45"
# Lunes 2026-11-02 08:00–10:00 en Lima (UTC-5): donde la heurística pondría el informe.
MORNING = {"start": "2026-11-02T13:00:00Z", "end": "2026-11-02T15:00:00Z"}

pytestmark = [pytest.mark.integration, pytest.mark.security]


def _visit(url: str) -> None:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        response.read()


class Calendar:
    def __init__(self, root: Path, vault: Path, google: FakeGoogle) -> None:
        self.root = root
        self.vault = vault
        self.google = google
        self.state = root / "state"
        self.environ = {
            "LIFEOS_VAULT_PATH": str(vault),
            "LIFEOS_LOGS_DIR": str(root / "logs"),
            "LIFEOS_STATE_DIR": str(self.state),
            "GOOGLE_CLIENT_ID": CLIENT_ID,
            "LIFEOS_GOOGLE_OAUTH": google.oauth_base,
            "LIFEOS_GOOGLE_API": google.api_base,
        }

    def browser(self, url: str) -> None:
        target = self.google.authorize(url)
        threading.Thread(target=_visit, args=(target,), daemon=True).start()

    def run(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = calendar_main(
            list(argv),
            environ=self.environ,
            project_root=self.root,
            out=out,
            err=err,
            open_browser=self.browser,
            clock=FakeClock(NOW),
        )
        return code, out.getvalue(), err.getvalue()

    def lifeos(self, *argv: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        code = lifeos_main(
            list(argv),
            environ=self.environ,
            project_root=self.root,
            clock=FakeClock(NOW),
            out=out,
            err=err,
        )
        return code, out.getvalue(), err.getvalue()

    def audit(self) -> list[dict[str, object]]:
        lines = []
        for path in sorted((self.root / "logs").glob("audit-*.jsonl")):
            lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        return lines


@pytest.fixture
def cal(tmp_path: Path, fake_google: FakeGoogle) -> Calendar:  # noqa: F811
    root = tmp_path / "project"
    shutil.copytree(PROJECT / "config", root / "config")
    vault = tmp_path / "vault"
    (vault / "_sistema").mkdir(parents=True)
    (vault / "01-Universidad").mkdir()
    (vault / "_sistema" / "horario.md").write_text(HORARIO, encoding="utf-8")
    (vault / "01-Universidad" / "redes.md").write_text(TASKS, encoding="utf-8")
    fake_google.busy = [MORNING]
    return Calendar(root, vault, fake_google)


def test_each_permission_is_connected_separately(cal: Calendar) -> None:
    code, out, _ = cal.run("estado")
    assert code == 0 and out.count("sin conectar") == 2
    assert cal.run("conectar", "--lectura")[0] == 0
    _, out, _ = cal.run("estado")
    assert "lectura: conectado" in out and "escritura: sin conectar" in out
    saved = json.loads((cal.state / "google-lectura.json").read_text(encoding="utf-8"))
    assert saved["scope"] == READ  # el token de lectura solo puede leer la disponibilidad
    assert not (cal.state / "google-escritura.json").exists()


def test_full_cycle_availability_plan_and_publication(cal: Calendar) -> None:
    cal.run("conectar", "--lectura")
    code, out, _ = cal.run("disponibilidad", "--semana", WEEK)
    assert code == 0
    assert "2026-11-02 08:00–10:00 · google:primary · confianza alta" in out

    assert cal.lifeos("semana", "--semana", WEEK)[0] == 0
    weekly = (cal.vault / "Semanal" / f"{WEEK}.md").read_text(encoding="utf-8")
    assert "08:00–10:00 · ocupado · fuente: google:primary · confianza: alta" in weekly
    assert "08:00–09:00 · Informe" not in weekly and "08:00–10:00 · Informe" not in weekly

    # Sin aprobar no se publica nada, aunque haya permiso de escritura.
    cal.run("conectar", "--escritura")
    code, _, err = cal.run("publicar", "--semana", WEEK, "--confirmar")
    assert code == 1 and "no está aprobado" in err
    assert cal.google.events() == []

    assert cal.lifeos("aprobar", "--semana", WEEK)[0] == 0
    code, out, _ = cal.run("publicar", "--semana", WEEK)
    assert code == 0 and "+ crear" in out and "Nada se publicó" in out
    assert cal.google.events() == []  # la vista previa no escribe

    code, out, _ = cal.run("publicar", "--semana", WEEK, "--confirmar")
    assert code == 0
    created = len(cal.google.events())
    assert created > 0 and f"Publicado: {created} creado(s)" in out
    assert all(str(e["summary"]).startswith("Life OS · ") for e in cal.google.events())

    code, out, _ = cal.run("publicar", "--semana", WEEK, "--confirmar")
    assert code == 0 and "Publicado: 0 creado(s), 0 ajustado(s), 0 borrado(s)" in out
    assert len(cal.google.events()) == created  # republicar no duplica

    actions = [(r["actor"], r["action"], r["result"]) for r in cal.audit()]
    assert actions.count(("jefe", "publicar_calendario", "ok")) == created


def test_read_token_cannot_publish(cal: Calendar) -> None:
    cal.run("conectar", "--lectura")
    cal.lifeos("semana", "--semana", WEEK)
    cal.lifeos("aprobar", "--semana", WEEK)
    code, _, err = cal.run("publicar", "--semana", WEEK, "--confirmar")
    assert code == 1 and "conectar --escritura" in err


def test_revocation_from_life_os(cal: Calendar) -> None:
    cal.run("conectar", "--lectura")
    cal.run("conectar", "--escritura")
    code, out, _ = cal.run("revocar")
    assert code == 0
    assert "Revocado en Google" in out and "myaccount.google.com/permissions" in out
    assert cal.google.revoked and cal.google.revocations == 1
    assert not list(cal.state.glob("google-lectura.json"))
    assert not list(cal.state.glob("google-escritura.json"))
    _, out, _ = cal.run("estado")
    assert out.count("sin conectar") == 2
    code, _, err = cal.run("disponibilidad", "--semana", WEEK)
    assert code == 1 and "conectar --lectura" in err
    # Sin calendario conectado, el Jefe sigue planificando sin eventos externos.
    assert cal.lifeos("semana", "--semana", WEEK)[0] == 0


def test_revocation_from_google_is_detected(cal: Calendar) -> None:
    cal.run("conectar", "--lectura")
    cal.google.revoked = True  # el propietario quitó el acceso en su cuenta de Google
    code, _, err = cal.run("disponibilidad", "--semana", WEEK)
    assert code == 1 and "revocado" in err
    assert not (cal.state / "google-lectura.json").exists()


def test_revocation_without_tokens_is_harmless(cal: Calendar) -> None:
    code, out, _ = cal.run("revocar")
    assert code == 0 and "No había tokens" in out


def test_missing_client_id_is_a_configuration_error(cal: Calendar) -> None:
    cal.environ.pop("GOOGLE_CLIENT_ID")
    code, _, err = cal.run("conectar", "--lectura")
    assert code == 2 and "GOOGLE_CLIENT_ID" in err


def test_invalid_week_is_a_usage_error(cal: Calendar) -> None:
    code, _, err = cal.run("publicar", "--semana", "2026-45")
    assert code == 2 and "AAAA-Wnn" in err
