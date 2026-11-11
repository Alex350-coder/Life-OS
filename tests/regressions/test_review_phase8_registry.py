"""Regresiones de la revisión de la Fase 8: registro y revisión de propuestas (security-reviewer,
python-reviewer, silent-failure-hunter y code-reviewer)."""

import threading
import time
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

from lifeos.adapters.agent_store import JsonAgentRegistry
from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.process_lock import FileLock, LockTimeoutError
from lifeos.adapters.yaml_config import parse_agent
from lifeos.application.agent_registry import AgentNotFoundError, AgentRegistry
from lifeos.application.proposal_review import ProposalReview, ProposalReviewError
from lifeos.application.tasks import PROPOSALS_ROOT
from lifeos.domain.agent_lifecycle import RegistryEntry
from lifeos.domain.area_proposals import (
    AreaProposal,
    ProposalItem,
    ProposalKind,
    ProposalRef,
    render_proposal,
)
from lifeos.domain.areas import PROPOSALS_ROOT as PROPOSALS_FOLDER
from lifeos.domain.task import Area, Effort, Priority
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.config import ConfigSyntaxError
from lifeos.ports.state import StateError
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.application.test_specialists import _validate
from tests.fakes import FakeClock, MemoryAuditLog

pytestmark = pytest.mark.integration


class _FailingSave(JsonAgentRegistry):
    fail = True

    def save(self, entry: RegistryEntry) -> None:
        if self.fail:
            raise StateError("registro.json bloqueado por la sincronización")
        super().save(entry)


def test_failed_save_does_not_block_a_later_import(tmp_path: Path) -> None:
    store = _FailingSave(tmp_path)
    registry = AgentRegistry(store, parse_agent, _validate, MemoryAuditLog(), FakeClock())
    with pytest.raises(StateError):
        registry.import_config(samples.dump(samples.specialist()).encode(), "u.yaml")

    store.fail = False
    data = samples.specialist()
    data["proposito"] = "Archivo corregido por el propietario."
    entry = registry.import_config(samples.dump(data).encode(), "u.yaml")

    assert entry.current == 1
    assert registry.export("universidad") == samples.dump(data).encode()


def test_view_of_unknown_agent_is_a_clear_error(tmp_path: Path) -> None:
    registry = AgentRegistry(
        JsonAgentRegistry(tmp_path), parse_agent, _validate, MemoryAuditLog(), FakeClock()
    )

    with pytest.raises(AgentNotFoundError):
        registry.view("nadie")


def test_registry_writes_are_serialized_across_processes(tmp_path: Path) -> None:
    order: list[str] = []
    lock_path = tmp_path / "agentes.lock"

    def exclusive() -> AbstractContextManager[object]:
        return FileLock(lock_path, timeout_s=5)

    registry = AgentRegistry(
        JsonAgentRegistry(tmp_path),
        parse_agent,
        _validate,
        MemoryAuditLog(),
        FakeClock(),
        exclusive=exclusive,
    )
    holder = FileLock(lock_path, timeout_s=5)
    holder.__enter__()

    def run() -> None:
        registry.import_config(samples.dump(samples.specialist()).encode(), "u.yaml")
        order.append("importado")

    worker = threading.Thread(target=run)
    worker.start()
    time.sleep(0.3)
    order.append("liberado")
    holder.__exit__(None, None, None)
    worker.join(timeout=5)

    assert order == ["liberado", "importado"]


def test_file_lock_times_out_with_clear_error(tmp_path: Path) -> None:
    holder = FileLock(tmp_path / "x.lock", timeout_s=5)
    with holder, pytest.raises(LockTimeoutError, match="ocupado"):
        FileLock(tmp_path / "x.lock", timeout_s=0.2).__enter__()


def _new_task_note(due: str) -> str:
    item = ProposalItem(
        "p1",
        ProposalKind.NUEVA_TAREA,
        Priority.MEDIA,
        "meta",
        title="Paso",
        effort=Effort.parse("1h"),
        due=__import__("datetime").date.fromisoformat(due),
    )
    proposal = AreaProposal(
        "universidad", Area.UNIVERSIDAD, IsoWeek.parse("2026-W40"), "heuristica", (item,)
    )
    return render_proposal(proposal)


def test_accepting_new_task_with_past_due_is_refused(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note("Propuestas/2026-W40/universidad.md", _new_task_note("2026-10-01"))
    review = ProposalReview(vault, MemoryAuditLog(), FakeClock())  # FakeClock: 2026-10-27

    with pytest.raises(ProposalReviewError, match="ya pasó"):
        review.accept(ProposalRef.parse("2026-W40/universidad/p1"))


def test_unreadable_or_misnamed_notes_are_reported_not_hidden(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note("Propuestas/2026-W40/universidad.md", _new_task_note("2026-12-01"))
    write_note("Propuestas/semana-rara/universidad.md", "x\n")
    write_note("Propuestas/2026-W40/Mis notas.md", "x\n")

    pending = ProposalReview(vault, MemoryAuditLog(), FakeClock()).pending()

    assert [str(i.ref) for i in pending.items] == ["2026-W40/universidad/p1"]
    assert len(pending.skipped) == 2
    assert any("Mis notas" in s for s in pending.skipped)


def test_single_proposals_root() -> None:
    assert str(PROPOSALS_ROOT) == PROPOSALS_FOLDER


def test_deeply_nested_yaml_is_a_syntax_error() -> None:
    with pytest.raises(ConfigSyntaxError):
        parse_agent(("[" * 5000 + "]" * 5000).encode(), "anidado.yaml")
