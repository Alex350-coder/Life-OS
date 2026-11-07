"""Bucle del servicio: offset, errores del canal con espera creciente y rituales (ADR-0014)."""

from dataclasses import dataclass, field

import pytest
from lifeos.service.main import BACKOFF_MAX_S, BACKOFF_START_S, ServiceLoop

from lifeos.application.tasks import ApplicationError
from lifeos.ports.chat import ChatError, IncomingMessage
from tests.service.fakes import FakeChannel, text

pytestmark = pytest.mark.unit


@dataclass
class FakeBot:
    handled: list[int] = field(default_factory=list)
    fail_on: int | None = None

    def handle(self, message: IncomingMessage) -> None:
        self.handled.append(message.update_id)
        if message.update_id == self.fail_on:
            raise ApplicationError("bóveda ocupada")


@dataclass
class FakeRituals:
    ticks: int = 0
    error: Exception | None = None

    def tick(self) -> object:
        self.ticks += 1
        if self.error is not None:
            raise self.error
        return ()


@dataclass
class Harness:
    channel: FakeChannel = field(default_factory=FakeChannel)
    bot: FakeBot = field(default_factory=FakeBot)
    rituals: FakeRituals = field(default_factory=FakeRituals)
    sleeps: list[float] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def loop(self) -> ServiceLoop:
        return ServiceLoop(
            self.channel, self.bot, self.rituals, sleep=self.sleeps.append, log=self.logs.append
        )


def test_messages_are_handled_and_the_offset_advances() -> None:
    h = Harness()
    h.channel.batches = [(text(10, "a"), text(11, "b")), ()]
    loop = h.loop()
    loop.run(iterations=2)
    assert h.bot.handled == [10, 11]
    assert h.channel.polls == [None, 12]
    assert h.rituals.ticks == 2


def test_a_failing_message_is_logged_and_does_not_block_the_next() -> None:
    h = Harness(bot=FakeBot(fail_on=10))
    h.channel.batches = [(text(10, "a"), text(11, "b"))]
    h.loop().run(iterations=1)
    assert h.bot.handled == [10, 11]
    assert any("bóveda ocupada" in line for line in h.logs)


def test_channel_errors_back_off_and_rituals_still_run() -> None:
    h = Harness()
    h.channel.batches = [ChatError("sin red")] * 12
    h.loop().run(iterations=12)
    assert h.sleeps[0] == BACKOFF_START_S
    assert h.sleeps[1] == BACKOFF_START_S * 2
    assert max(h.sleeps) == BACKOFF_MAX_S
    assert h.rituals.ticks == 12


def test_backoff_resets_after_a_successful_poll() -> None:
    h = Harness()
    h.channel.batches = [ChatError("sin red"), ChatError("sin red"), (), ChatError("sin red")]
    h.loop().run(iterations=4)
    assert h.sleeps == [BACKOFF_START_S, BACKOFF_START_S * 2, BACKOFF_START_S]


def test_repeated_ritual_errors_are_logged_once_until_they_change() -> None:
    h = Harness(rituals=FakeRituals(error=ApplicationError("falta _sistema/rituales.md")))
    h.loop().run(iterations=3)
    assert len([line for line in h.logs if "rituales.md" in line]) == 1
