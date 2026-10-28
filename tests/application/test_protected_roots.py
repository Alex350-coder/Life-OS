"""Las carpetas protegidas de los casos de uso salen de la política y no bajan del mínimo E.12."""

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.yaml_config import parse_policy
from lifeos.application.archive import restore_task
from lifeos.application.permissions import protected_roots
from lifeos.application.tasks import (
    DEFAULT_PROTECTED_ROOTS,
    ApplicationError,
    check_writable,
    create_task,
    ensure_id,
)
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Area, Effort
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples
from tests.application.conftest import WriteNote

pytestmark = [pytest.mark.integration, pytest.mark.security]

TASK = "- [ ] Leer [id:: t-222222] [area:: universidad] [esfuerzo:: 1h]"


def _policy_protected(*extra: str) -> tuple[VaultPath, ...]:
    data = samples.policy()
    data["global"]["escritura_prohibida_para_agentes"].extend(extra)
    return protected_roots(parse_policy(samples.dump(data).encode()))


def test_policy_extra_roots_are_enforced_by_task_use_cases(vault: FilesystemVault) -> None:
    protected = _policy_protected("Privado/")
    with pytest.raises(ApplicationError, match="protegida"):
        create_task(
            vault,
            VaultPath("privado/x.md"),
            title="x",
            area=Area.SALUD,
            effort=Effort(30),
            protected=protected,
        )
    with pytest.raises(ApplicationError):
        check_writable(VaultPath("Privado/sub/x.md"), protected)


def test_ensure_id_and_restore_use_the_injected_roots(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    protected = _policy_protected("Privado/")
    write_note("Privado/x.md", TASK.replace(" [id:: t-222222]", "") + "\n")
    with pytest.raises(ApplicationError):
        ensure_id(vault, VaultPath("Privado/x.md"), 0, protected=protected)
    write_note("Archivo/Privado/y.md", TASK + "\n")
    with pytest.raises(ApplicationError):
        restore_task(vault, TaskId("t-222222"), protected=protected)


def test_default_roots_are_exactly_the_rules_floor_plus_inbox() -> None:
    assert {r.value for r in DEFAULT_PROTECTED_ROOTS} == {
        "_sistema",
        "_logs",
        "Archivo",
        "00-Inbox",
    }
    assert set(DEFAULT_PROTECTED_ROOTS) <= set(_policy_protected())
