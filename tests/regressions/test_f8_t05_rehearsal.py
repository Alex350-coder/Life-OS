"""Fallos encontrados al ensayar F8-T05 sobre una copia de `vault-template` (2026-11-16)."""

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.proposal_review import ProposalReview
from lifeos.domain.area_proposals import AreaProposal, ProposalItem, ProposalKind, render_proposal
from lifeos.domain.task import Area, Effort, Priority
from lifeos.domain.weeks import IsoWeek
from tests.application.conftest import WriteNote
from tests.fakes import FakeClock, MemoryAuditLog

pytestmark = pytest.mark.integration

WEEK = IsoWeek.parse("2026-W44")


def _proposal() -> AreaProposal:
    item = ProposalItem(
        "p1",
        ProposalKind.NUEVA_TAREA,
        Priority.MEDIA,
        "meta m1",
        title="Siguiente paso",
        effort=Effort.parse("1h"),
    )
    return AreaProposal("universidad", Area.UNIVERSIDAD, WEEK, "heuristica", (item,))


def test_template_readme_in_proposals_root_is_not_reported_as_skipped(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    # vault-template trae Propuestas/README.md: no es una nota de propuestas ni un problema.
    write_note("Propuestas/README.md", "# Propuestas\n")
    write_note("Propuestas/2026-W44/universidad.md", render_proposal(_proposal()))

    pending = ProposalReview(vault, MemoryAuditLog(), FakeClock()).pending()

    assert [str(i.ref) for i in pending.items] == ["2026-W44/universidad/p1"]
    assert pending.skipped == ()


def test_other_stray_notes_in_proposals_root_are_still_reported(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note("Propuestas/notas sueltas.md", "x\n")

    pending = ProposalReview(vault, MemoryAuditLog(), FakeClock()).pending()

    assert len(pending.skipped) == 1


def test_proposal_note_names_the_real_review_command() -> None:
    text = render_proposal(_proposal())

    assert "`lifeos-agentes propuestas`" in text
    assert "`lifeos propuestas`" not in text
