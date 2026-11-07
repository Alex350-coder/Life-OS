"""Ejecutor de rituales: horas, silencio, recuperación y reintentos (F6-T01, F6-T02, ADR-0014)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.application.coach import CoachSession
from lifeos.application.jefe import JefePlanner
from lifeos.application.rituals import MAX_ATTEMPTS, RitualRunner
from lifeos.application.tools import ToolRegistry
from lifeos.domain.rituals import RitualesError
from lifeos.ports.logs import AuditResult
from lifeos.ports.notify import NotifyError
from lifeos.ports.state import RitualMark, RitualStatus
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.daily_builders import TASKS, daily_text, protected_line, work_line
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import MON, SAT, SUN, at
from tests.planning_samples import horario

pytestmark = pytest.mark.integration

HORARIO = horario(
    disponibilidad="- lun-dom 08:00–24:00",
    protegido="- lun-dom 13:00–14:00 · Almuerzo",
)
WEEKLY_PLACEHOLDER = "---\ntipo: semanal\n---\n\n## Check-ins\n"
RITUALES = """---
tipo: rituales
---

## Rituales
- briefing · lun-sáb 09:00
- cierre · lun-dom 22:30
- propuesta_semanal · dom 17:00
- recordatorio · 5m antes

## No molestar
- lun-dom 23:30–24:00
"""


@dataclass
class FakeNotifier:
    sent: list[str] = field(default_factory=list)
    failures: int = 0  # cuántos envíos fallan antes de funcionar

    def send(self, text: str) -> None:
        if self.failures:
            self.failures -= 1
            raise NotifyError("telegram no disponible")
        self.sent.append(text)


@dataclass
class MemoryState:
    marks: dict[str, RitualMark] = field(default_factory=dict)

    def load(self) -> dict[str, RitualMark]:
        return dict(self.marks)

    def save(self, marks: Mapping[str, RitualMark]) -> None:
        self.marks = dict(marks)


@dataclass
class Harness:
    runner: RitualRunner
    clock: FakeClock
    notifier: FakeNotifier
    state: MemoryState
    audit: MemoryAuditLog

    def tick_at(self, moment: datetime) -> None:
        self.clock.current = moment
        self.clock.elapsed = 0
        self.runner.tick()

    def status(self, key: str) -> RitualStatus | None:
        mark = self.state.marks.get(key)
        return mark.status if mark else None


@pytest.fixture
def seeded(write_note: WriteNote) -> None:
    write_note("_sistema/horario.md", HORARIO)
    write_note("_sistema/rituales.md", RITUALES)
    write_note("01-Universidad/redes.md", TASKS)
    # La semana en curso ya tiene plan: la propuesta del domingo anterior no se recupera.
    write_note("Semanal/2026-W45.md", WEEKLY_PLACEHOLDER)
    write_note(
        "Diario/2026-11-02.md",
        daily_text(
            MON,
            work=(
                work_line("10:00–11:00", "Informe de redes", "t-aaaaaa"),
                work_line("14:00–15:00", "Leer capítulo", "t-bbbbbb"),
            ),
            protected=(protected_line("13:00–14:00", "Almuerzo"),),
        ),
    )


@pytest.fixture
def harness(vault: FilesystemVault, seeded: None) -> Harness:
    clock = FakeClock(at(MON, "08:00"))
    audit = MemoryAuditLog()
    tools = ToolRegistry(vault, samples.effective(), audit, clock)
    notifier, state = FakeNotifier(), MemoryState()
    runner = RitualRunner(
        vault,
        JefePlanner(tools, clock),
        CoachSession(tools, clock),
        notifier,
        state,
        audit,
        clock,
    )
    return Harness(runner, clock, notifier, state, audit)


# --- Hora y deduplicación -------------------------------------------------------------------


def test_nothing_runs_before_its_time(harness: Harness) -> None:
    harness.tick_at(at(MON, "08:30"))
    assert harness.notifier.sent == []


def test_briefing_runs_once_at_its_time_with_the_day_plan(harness: Harness) -> None:
    harness.tick_at(at(MON, "09:00"))
    harness.tick_at(at(MON, "09:01"))
    (message,) = harness.notifier.sent
    assert "Informe de redes" in message
    assert "10:00" in message
    assert harness.status("briefing:2026-11-02") is RitualStatus.HECHO
    ritual_audit = [r for r in harness.audit.records if r.target.startswith("briefing:")]
    assert [(r.target, r.result) for r in ritual_audit] == [("briefing:2026-11-02", AuditResult.OK)]


def test_briefing_without_a_daily_plan_says_so(harness: Harness, write_note: WriteNote) -> None:
    harness.tick_at(at(SAT, "09:00"))
    (message,) = harness.notifier.sent
    assert "no hay plan aprobado" in message.casefold()


def test_reminder_comes_before_the_block_and_never_twice(harness: Harness) -> None:
    harness.tick_at(at(MON, "09:00"))  # briefing
    harness.tick_at(at(MON, "09:55"))
    harness.tick_at(at(MON, "09:57"))
    reminders = [m for m in harness.notifier.sent if "Informe de redes" in m][1:]
    assert len(reminders) == 1
    assert "10:00" in reminders[0]
    assert harness.status("recordatorio:2026-11-02:t-aaaaaa:10:00") is RitualStatus.HECHO


def test_reminder_inside_protected_time_waits_for_its_end(harness: Harness) -> None:
    # Bloque a las 14:00 tras el almuerzo 13–14: el aviso de las 13:55 espera a las 14:00.
    harness.tick_at(at(MON, "13:56"))
    assert not any("Leer capítulo" in m for m in harness.notifier.sent)
    harness.tick_at(at(MON, "14:00"))
    assert any("Leer capítulo" in m for m in harness.notifier.sent)


# --- Silencio y recuperación ------------------------------------------------------------------


def test_missed_ritual_beyond_its_grace_is_skipped_and_audited(harness: Harness) -> None:
    harness.tick_at(at(MON, "12:30"))  # el briefing de las 09:00 tiene 3 h de margen
    assert not any("Plan de hoy" in m for m in harness.notifier.sent)
    assert harness.status("briefing:2026-11-02") is RitualStatus.OMITIDO
    skipped = [r for r in harness.audit.records if r.target == "briefing:2026-11-02"]
    assert skipped[-1].result is AuditResult.RECHAZADO
    assert skipped[-1].reason == "omitido: fuera de su margen"


def test_late_catch_up_runs_within_its_grace_and_only_once(harness: Harness) -> None:
    # El PC se enciende a las 11:50: el briefing de las 09:00 aún está dentro de su margen.
    harness.tick_at(at(MON, "11:50"))
    assert harness.status("briefing:2026-11-02") is RitualStatus.HECHO
    harness.notifier.sent.clear()
    harness.tick_at(at(MON, "22:30"))
    harness.tick_at(at(MON, "23:45"))  # el cierre ya se envió a las 22:30; no molestar no repite
    assert len([m for m in harness.notifier.sent if "cierre" in m.casefold()]) == 1


def test_catch_up_never_notifies_during_sleep(harness: Harness) -> None:
    # Encendido a las 00:10: el cierre de ayer (22:30) aún tiene margen, pero es hora de dormir.
    harness.tick_at(at(MON, "00:10") + timedelta(days=1))
    assert harness.notifier.sent == []
    assert harness.status("cierre:2026-11-02") is None  # pendiente, no se pierde todavía
    harness.tick_at(at(MON, "08:30") + timedelta(days=1))
    assert harness.notifier.sent == []
    assert harness.status("cierre:2026-11-02") is RitualStatus.OMITIDO


def test_do_not_disturb_drops_the_notification(
    harness: Harness, write_note: WriteNote, vault: FilesystemVault
) -> None:
    text = vault.read(VaultPath.parse("_sistema/rituales.md")).text
    vault.write(
        VaultPath.parse("_sistema/rituales.md"),
        text.replace("cierre · lun-dom 22:30", "cierre · lun-dom 23:40"),
        expected=vault.read(VaultPath.parse("_sistema/rituales.md")).version,
    )
    harness.tick_at(at(MON, "23:41"))
    assert harness.notifier.sent == []
    assert harness.status("cierre:2026-11-02") is RitualStatus.DESCARTADO


# --- Errores y reintentos ---------------------------------------------------------------------


def test_failed_send_is_retried_on_the_next_tick(harness: Harness) -> None:
    harness.notifier.failures = 1
    harness.tick_at(at(MON, "09:00"))
    mark = harness.state.marks["briefing:2026-11-02"]
    assert (mark.status, mark.attempts) == (RitualStatus.FALLIDO, 1)
    harness.tick_at(at(MON, "09:01"))
    assert harness.status("briefing:2026-11-02") is RitualStatus.HECHO
    assert len(harness.notifier.sent) == 1
    results = [r.result for r in harness.audit.records if r.target == "briefing:2026-11-02"]
    assert results == [AuditResult.ERROR, AuditResult.OK]


def test_retries_stop_after_the_maximum(harness: Harness) -> None:
    harness.notifier.failures = MAX_ATTEMPTS + 2
    for step in range(MAX_ATTEMPTS + 2):  # cada vuelta, tras la espera máxima entre reintentos
        harness.tick_at(at(MON, "09:00") + timedelta(minutes=10 * step))
    mark = harness.state.marks["briefing:2026-11-02"]
    assert (mark.status, mark.attempts) == (RitualStatus.FALLIDO, MAX_ATTEMPTS)


def test_invalid_rituales_note_stops_the_tick_without_sending(
    harness: Harness, vault: FilesystemVault
) -> None:
    path = VaultPath.parse("_sistema/rituales.md")
    vault.write(path, "## Rituales\n- siesta · lun 09:00\n", expected=vault.read(path).version)
    with pytest.raises(RitualesError):
        harness.tick_at(at(MON, "09:00"))
    assert harness.notifier.sent == []


def test_old_marks_are_pruned(harness: Harness) -> None:
    old = RitualMark(RitualStatus.HECHO, 1, at(MON, "09:00") - timedelta(days=30), None)
    harness.state.marks["briefing:2026-10-01"] = old
    harness.tick_at(at(MON, "08:30"))
    assert "briefing:2026-10-01" not in harness.state.marks


# --- Propuesta semanal ------------------------------------------------------------------------


def test_weekly_proposal_drafts_next_week_once_and_stays_proposed(
    harness: Harness, vault: FilesystemVault
) -> None:
    harness.tick_at(at(SUN, "17:00"))
    note = vault.read(VaultPath.parse("Semanal/2026-W46.md")).text
    assert "estado_plan: propuesto" in note
    (message,) = [m for m in harness.notifier.sent if "2026-W46" in m]
    assert "aprueba" in message.casefold()
    harness.tick_at(at(SUN, "17:05"))
    assert len([m for m in harness.notifier.sent if "2026-W46" in m]) == 1


def test_weekly_proposal_skips_a_week_that_already_has_a_plan(
    harness: Harness, write_note: WriteNote
) -> None:
    write_note("Semanal/2026-W46.md", "---\ntipo: semanal\nsemana: 2026-W46\n---\n")
    harness.tick_at(at(SUN, "17:00"))
    assert not any("2026-W46" in m for m in harness.notifier.sent)
    assert harness.status("propuesta_semanal:2026-11-08") is RitualStatus.HECHO
