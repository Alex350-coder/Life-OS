"""Regresiones de la revisión de la Fase 6 (python-reviewer, security-reviewer y
silent-failure-hunter). Cada prueba reproduce un hallazgo antes de corregirlo."""

import contextlib
import hashlib
import json
import sys
import types
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.secrets import Secret
from lifeos.adapters.telegram import MAX_MESSAGE_CHARS, TelegramBotClient, _split
from lifeos.adapters.whisper import FasterWhisperTranscriber
from lifeos.application.coach import CoachSession
from lifeos.application.rituals import RitualReport
from lifeos.application.tools import ToolRegistry
from lifeos.domain.capacity import day_capacity
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.quiet import DeliveryKind, plan_delivery, quiet_windows
from lifeos.domain.rituals import parse_rituales, ritual_occurrences
from lifeos.ports.chat import IncomingMessage
from lifeos.ports.logs import AuditResult, LogWriteError
from lifeos.ports.state import RitualMark, RitualStatus, StateError
from lifeos.ports.transcribe import TranscriptionError
from lifeos.ports.vault import VaultIOError, VaultPath
from lifeos.service.bot import OwnerBot, chat_fingerprint
from lifeos.service.main import ServiceLoop
from tests import config_samples as samples
from tests.application.conftest import WriteNote
from tests.application.test_ritual_runner import Harness, MemoryState
from tests.application.test_ritual_runner import harness as harness  # fixture compartida
from tests.application.test_ritual_runner import seeded as seeded  # fixture compartida
from tests.fakes import FakeClock, MemoryAuditLog
from tests.planning_builders import MON, SUN, at
from tests.planning_samples import horario
from tests.service.fakes import OWNER, STRANGER, FakeChannel, text
from tests.stub_http import Reply, StubServer

pytestmark = [pytest.mark.security, pytest.mark.integration]
TOKEN = "123456:ABC-token-de-prueba_xyz"  # noqa: S105 - token inventado para las pruebas
KEY = b"clave-local-de-prueba"


# --- Runner: estado, auditoría y reintentos (C1, H2, H3, M1) ---------------------------------


@dataclass
class FlakyState(MemoryState):
    fail_saves: int = 0

    def save(self, marks: Mapping[str, RitualMark]) -> None:
        if self.fail_saves:
            self.fail_saves -= 1
            raise StateError("rituales.json bloqueado por OneDrive")
        super().save(marks)


def test_state_save_failure_after_sending_never_resends(harness: Harness) -> None:
    flaky = FlakyState(fail_saves=100)
    harness.runner._state = flaky
    for minute in range(5):
        harness.clock.current = at(MON, f"09:0{minute}")
        with contextlib.suppress(StateError):  # el fallo se informa, pero no repite el envío
            harness.runner.tick()
    assert len([m for m in harness.notifier.sent if "Plan de hoy" in m]) == 1


def test_audit_failure_after_sending_never_resends(harness: Harness) -> None:
    harness.audit.fail_on = AuditResult.OK
    for minute in range(3):
        harness.clock.current = at(MON, f"09:0{minute}")
        with contextlib.suppress(LogWriteError):  # el fallo se informa, pero no repite el envío
            harness.runner.tick()
    assert len([m for m in harness.notifier.sent if "Plan de hoy" in m]) == 1


def test_retries_are_spaced_out_within_the_grace(harness: Harness) -> None:
    harness.notifier.failures = 2
    harness.tick_at(at(MON, "09:00"))
    harness.tick_at(at(MON, "09:00") + timedelta(seconds=20))  # demasiado pronto
    assert harness.state.marks["briefing:2026-11-02"].attempts == 1
    harness.tick_at(at(MON, "09:02"))
    harness.tick_at(at(MON, "09:10"))
    assert harness.status("briefing:2026-11-02") is RitualStatus.HECHO


def test_weekly_proposal_retry_still_notifies_the_existing_proposal(harness: Harness) -> None:
    harness.notifier.failures = 1
    harness.tick_at(at(SUN, "17:00"))  # se propone W46, pero el aviso falla
    harness.tick_at(at(SUN, "17:10"))
    assert any("2026-W46" in m for m in harness.notifier.sent)


def test_unreadable_daily_plan_does_not_stop_the_other_rituals(
    harness: Harness, vault: FilesystemVault
) -> None:
    path = VaultPath.parse("Diario/2026-11-02.md")
    vault.write(
        path,
        "---\nestado_plan: aprobado\n---\n## Bloques\n- roto",
        expected=vault.read(path).version,
    )
    harness.tick_at(at(MON, "22:30"))
    assert harness.status("cierre:2026-11-02") is RitualStatus.HECHO


def test_hard_silence_is_audited_as_discarded_even_after_the_grace(
    harness: Harness, vault: FilesystemVault
) -> None:
    path = VaultPath.parse("_sistema/rituales.md")
    text_ = vault.read(path).text.replace("cierre · lun-dom 22:30", "cierre · lun-dom 23:40")
    vault.write(path, text_, expected=vault.read(path).version)
    harness.tick_at(at(MON, "23:40") + timedelta(hours=3))
    assert harness.status("cierre:2026-11-02") is RitualStatus.DESCARTADO


# --- Dominio: silencio y cambios de hora (M5, M6) ---------------------------------------------


def test_reminder_before_availability_waits_for_it_instead_of_being_dropped() -> None:
    schedule = parse_horario(horario(disponibilidad="- lun-dom 09:00–20:00"))
    day = date(2026, 11, 10)
    zone = ZoneInfo("America/Lima")
    windows = quiet_windows(day_capacity(schedule, day), (), day, zone)
    start = datetime.combine(day, time(9), tzinfo=zone)
    delivery = plan_delivery(start - timedelta(minutes=10), start, windows)
    assert (delivery.kind, delivery.at) == (DeliveryKind.APLAZAR, start)


def test_ritual_at_a_nonexistent_local_time_is_skipped_not_fatal() -> None:
    config = parse_rituales("## Rituales\n- briefing · dom 02:30\n- cierre · dom 21:00\n")
    madrid = ZoneInfo("Europe/Madrid")
    (only,) = ritual_occurrences(config, date(2026, 3, 29), madrid)  # 02:30 no existe ese día
    assert only.key == "cierre:2026-03-29"


# --- Telegram (H1, LOW) --------------------------------------------------------------------------


def _client(stub: StubServer) -> TelegramBotClient:
    return TelegramBotClient(Secret(TOKEN), base_url=stub.base_url.removesuffix("/v1"))


def test_one_malformed_update_does_not_block_the_batch(stub_server: StubServer) -> None:
    result = [
        {"update_id": 5, "message": {"chat": {"id": 1}, "voice": {"file_size": 3}}},
        {"update_id": 6, "message": {"chat": {"id": 1}, "text": "hola"}},
    ]
    stub_server.replies.append(Reply(body=json.dumps({"ok": True, "result": result}).encode()))
    bad, good = _client(stub_server).poll(None, 0)
    assert (bad.update_id, bad.chat_id, bad.voice) == (5, None, None)
    assert good.text == "hola"


def test_split_measures_utf16_keeps_blank_lines_and_skips_empty_text() -> None:
    emoji = "🎸" * (MAX_MESSAGE_CHARS // 2 + 10)  # cada uno ocupa 2 unidades UTF-16
    assert all(len(c.encode("utf-16-le")) // 2 <= MAX_MESSAGE_CHARS for c in _split(emoji))
    assert "".join(_split(emoji)) == emoji
    assert _split("a\n\nb") == ["a\n\nb"]
    assert _split("") == []


def test_telegram_base_url_must_be_the_official_host_or_loopback() -> None:
    with pytest.raises(ValueError, match=r"api\.telegram\.org"):
        TelegramBotClient(Secret(TOKEN), base_url="https://telegram.example.org")


def test_token_with_non_ascii_digits_is_rejected() -> None:
    with pytest.raises(ValueError):
        TelegramBotClient(Secret("١٢٣٤٥٦:ABC-token-de-prueba_xyz"))


# --- Whisper (C2) --------------------------------------------------------------------------------


def test_model_load_failure_is_a_transcription_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class Broken:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise OSError("no se pudo cargar ctranslate2.dll")

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = Broken  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    with pytest.raises(TranscriptionError, match="OSError"):
        FasterWhisperTranscriber("small").transcribe(b"audio")


# --- Bot (SEC-12, H4) ----------------------------------------------------------------------------


class BrokenVault(FilesystemVault):
    def write(self, *args: object, **kwargs: object) -> object:  # type: ignore[override]
        raise VaultIOError("disco lleno")


def _bot(
    vault: FilesystemVault, audit: MemoryAuditLog | None = None
) -> tuple[OwnerBot, FakeChannel, MemoryAuditLog]:
    clock = FakeClock(at(MON, "09:00"))
    log = audit or MemoryAuditLog()
    coach = CoachSession(ToolRegistry(vault, samples.effective(), log, clock), clock)
    channel = FakeChannel()
    bot = OwnerBot(
        channel,
        OWNER,
        vault=vault,
        coach=coach,
        clock=clock,
        audit=log,
        transcriber=None,
        fingerprint_key=KEY,
    )
    return bot, channel, log


def test_fingerprint_is_keyed_and_not_a_plain_hash(vault: FilesystemVault) -> None:
    bot, _, audit = _bot(vault)
    bot.handle(text(1, "hola", chat_id=STRANGER))
    plain = hashlib.sha256(str(STRANGER).encode()).hexdigest()[:12]
    assert plain not in audit.records[0].target
    assert audit.records[0].target == f"chat:{chat_fingerprint(STRANGER, KEY)}"


def test_foreign_spam_is_audited_once_per_hour(vault: FilesystemVault) -> None:
    bot, _, audit = _bot(vault)
    for update_id in range(50):
        bot.handle(text(update_id, "spam", chat_id=STRANGER))
    assert len(audit.records) == 1


def test_vault_failure_on_capture_still_answers_the_owner(root: object) -> None:
    bot, channel, _ = _bot(BrokenVault(root))  # type: ignore[arg-type]
    bot.handle(text(1, "comprar cuerdas"))
    assert "No pude guardarlo" in channel.sent[0][1]


def test_hoy_with_a_broken_schedule_answers_instead_of_failing(
    vault: FilesystemVault, write_note: WriteNote
) -> None:
    write_note("_sistema/horario.md", "## Límites\n- [foco_max_dia:: ]\n")
    bot, channel, _ = _bot(vault)
    bot.handle(text(1, "/hoy"))
    assert "No pude leer el plan" in channel.sent[0][1]


# --- Bucle (C2, H3) ----------------------------------------------------------------------------


@dataclass
class ExplodingBot:
    handled: list[int] = field(default_factory=list)

    def handle(self, message: IncomingMessage) -> None:
        self.handled.append(message.update_id)
        if message.update_id == 1:
            raise KeyError("fallo de programación")


@dataclass
class ReportingRituals:
    reports: tuple[RitualReport, ...] = ()
    explode: bool = False

    def tick(self) -> tuple[RitualReport, ...]:
        if self.explode:
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "byte inválido")
        return self.reports


def test_unexpected_exceptions_never_kill_the_loop() -> None:
    channel = FakeChannel(batches=[(text(1, "a"), text(2, "b"))])
    bot, logs = ExplodingBot(), list[str]()
    rituals = ReportingRituals(explode=True)
    ServiceLoop(channel, bot, rituals, sleep=lambda s: None, log=logs.append).run(iterations=2)
    assert bot.handled == [1, 2]
    assert any("KeyError" in line for line in logs)
    assert any("UnicodeDecodeError" in line for line in logs)


def test_failed_rituals_are_reported_on_stderr() -> None:
    report = RitualReport("briefing:2026-11-09", RitualStatus.FALLIDO, "NotifyError: caído")
    logs: list[str] = []
    ServiceLoop(
        FakeChannel(),
        ExplodingBot(),
        ReportingRituals(reports=(report,)),
        sleep=lambda s: None,
        log=logs.append,
    ).run(iterations=1)
    assert any("briefing:2026-11-09" in line and "caído" in line for line in logs)


def test_old_failed_mark_is_not_retried_after_the_limit() -> None:
    # Sanidad del contrato del estado: un FALLIDO agotado no vuelve a contar como pendiente.
    from lifeos.application.rituals import MAX_ATTEMPTS, _retryable

    mark = RitualMark(RitualStatus.FALLIDO, MAX_ATTEMPTS, at(MON, "09:00"))
    assert not _retryable(mark, at(MON, "12:00"))


# --- code-reviewer (revisión final) -------------------------------------------------------------


def test_do_not_disturb_on_a_dst_change_still_silences() -> None:
    # 2026-10-25 en Madrid: 02:00–03:00 ocurre dos veces. El rango no puede desaparecer.
    madrid = ZoneInfo("Europe/Madrid")
    day = date(2026, 10, 25)
    schedule = parse_horario(horario(zona="Europe/Madrid", disponibilidad="- lun-dom 00:00–24:00"))
    windows = quiet_windows(
        day_capacity(schedule, day),
        parse_rituales("## No molestar\n- dom 01:30–02:30\n").quiet_for(day),
        day,
        madrid,
    )
    moment = datetime.combine(day, time(1, 45), tzinfo=madrid)
    delivery = plan_delivery(moment, moment + timedelta(hours=3), windows)
    assert (delivery.kind, delivery.reason) == (DeliveryKind.DESCARTAR, "no molestar")


def test_whitespace_only_text_produces_no_message() -> None:
    assert _split(" \n \n") == []
