"""Raíz de composición: valida la configuración real al arrancar y cablea permisos y logs."""

import shutil
from pathlib import Path

import pytest

from lifeos.adapters.secrets import MissingSecretError
from lifeos.bootstrap import Settings, build_runtime
from lifeos.ports.config import ConfigRejectedError, ConfigSyntaxError
from lifeos.ports.vault import VaultPath

PROJECT = Path(__file__).resolve().parent.parent
KEY = "nvapi" + "-TESTKEY-0123456789abcdefXYZ"  # en dos partes: sin patrón real en el archivo

pytestmark = [pytest.mark.integration]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(PROJECT / "config", tmp_path / "config")
    return tmp_path


def test_runtime_from_shipped_config_without_llm(project: Path, tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings.from_env(project, {"LIFEOS_VAULT_PATH": str(vault)})
    runtime = build_runtime(settings)
    assert set(runtime.permissions) == {"jefe", "coach"}
    assert runtime.gateway is None
    assert runtime.tools is not None
    assert {"_sistema", "_logs", "Archivo", "00-Inbox"} <= {r.value for r in runtime.protected}
    assert settings.logs_dir == project / "logs"


def test_llm_runtime_requires_the_key_and_never_logs_it(project: Path) -> None:
    with pytest.raises(MissingSecretError, match="NVIDIA_API_KEY"):
        build_runtime(Settings.from_env(project, {}), with_llm=True)
    (project / ".env").write_text(f"NVIDIA_API_KEY={KEY}\n", encoding="utf-8")
    runtime = build_runtime(Settings.from_env(project, {}), with_llm=True)
    assert runtime.gateway is not None
    assert KEY not in repr(runtime.gateway.__dict__)


def test_process_environment_wins_over_dotenv(project: Path) -> None:
    (project / ".env").write_text("LIFEOS_LOGS_DIR=desde-archivo\n", encoding="utf-8")
    settings = Settings.from_env(project, {"LIFEOS_LOGS_DIR": "desde-entorno"})
    assert settings.logs_dir == Path("desde-entorno")


def test_invalid_policy_stops_startup(project: Path) -> None:
    policy = project / "config" / "permissions.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace("    - _logs/", "    - _otros/"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigRejectedError, match="_logs"):
        build_runtime(Settings.from_env(project, {}))


def test_tagged_agent_file_stops_startup(project: Path) -> None:
    coach = project / "config" / "agents" / "coach.yaml"
    coach.write_text(
        coach.read_text(encoding="utf-8") + "x: !!python/object/apply:os.system ['x']\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigSyntaxError):
        build_runtime(Settings.from_env(project, {}))


def test_policy_protected_roots_reach_the_runtime(project: Path) -> None:
    policy = project / "config" / "permissions.yaml"
    policy.write_text(
        policy.read_text(encoding="utf-8").replace(
            "    - Archivo/", "    - Archivo/\n    - Privado/"
        ),
        encoding="utf-8",
    )
    runtime = build_runtime(Settings.from_env(project, {}))
    assert VaultPath("Privado") in runtime.protected
