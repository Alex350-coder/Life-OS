"""Raíz de composición y Google Calendar (ADR-0017, RULES G.1).

Sin GOOGLE_CLIENT_ID la integración está apagada. Con él, cada permiso solo se conecta si su
token existe: tener el de lectura nunca da escritura.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lifeos.adapters.google_tokens import Grant, StoredToken, TokenStore
from lifeos.adapters.secrets import Secret
from lifeos.bootstrap import Settings, build_runtime, calendar_access
from lifeos.ports.config import ConfigRejectedError
from lifeos.ports.logs import AuditRecord, AuditResult, LogWriteError

PROJECT = Path(__file__).resolve().parent.parent
CLIENT_ID = "123-prueba.apps.googleusercontent.com"
SECRET = "GOCSPX-" + "secreto-de-prueba"  # en dos partes: sin patrón real en el archivo

pytestmark = [pytest.mark.integration, pytest.mark.security]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    shutil.copytree(PROJECT / "config", tmp_path / "config")
    (tmp_path / "vault").mkdir()
    return tmp_path


def env(project: Path, **extra: str) -> dict[str, str]:
    return {
        "LIFEOS_VAULT_PATH": str(project / "vault"),
        "LIFEOS_STATE_DIR": str(project / "state"),
        "LIFEOS_TOKENS_DIR": str(project / "state"),
        **extra,
    }


def save(project: Path, grant: Grant) -> None:
    token = StoredToken(
        grant, Secret("1//refresh"), grant.scope, datetime(2026, 11, 10, tzinfo=UTC)
    )
    TokenStore(project / "state").save(token)


def test_without_client_id_the_integration_is_off(project: Path) -> None:
    save(project, Grant.LECTURA)  # aunque haya un token, sin cliente no se usa
    runtime = build_runtime(Settings.from_env(project, env(project)))
    assert runtime.calendar is not None
    assert not runtime.calendar.can_read and not runtime.calendar.can_write
    with pytest.raises(ConfigRejectedError, match="GOOGLE_CLIENT_ID"):
        calendar_access(Settings.from_env(project, env(project)))


def test_each_permission_needs_its_own_token(project: Path) -> None:
    save(project, Grant.LECTURA)
    settings = Settings.from_env(project, env(project, GOOGLE_CLIENT_ID=CLIENT_ID))
    runtime = build_runtime(settings)
    assert runtime.calendar is not None
    assert runtime.calendar.can_read and not runtime.calendar.can_write
    assert runtime.calendar_publisher is not None


def test_invalid_client_id_stops_startup(project: Path) -> None:
    settings = Settings.from_env(project, env(project, GOOGLE_CLIENT_ID="no-es-de-google"))
    with pytest.raises(ConfigRejectedError, match="GOOGLE_CLIENT_ID"):
        build_runtime(settings)


def test_client_secret_is_redacted_from_the_logs(project: Path) -> None:
    settings = Settings.from_env(
        project, env(project, GOOGLE_CLIENT_ID=CLIENT_ID, GOOGLE_CLIENT_SECRET=SECRET)
    )
    runtime = build_runtime(settings)
    assert SECRET not in repr(settings)
    record = AuditRecord(
        ts=datetime(2026, 11, 10, tzinfo=UTC),
        correlation_id="c-1",
        actor="jefe",
        action="publicar_calendario",
        target=f"calendario:{SECRET}",
        result=AuditResult.ERROR,
    )
    with pytest.raises(LogWriteError):  # SEC-08: una línea con el secreto no se escribe
        runtime.audit_log.append(record)
    logs = list((project / "logs").glob("*.jsonl")) if (project / "logs").exists() else []
    assert all(SECRET not in path.read_text(encoding="utf-8") for path in logs)
