"""Entrada `lifeos-servicio` (ADR-0014): rituales a su hora y bot de Telegram en este PC.

Un solo proceso y un solo hilo: espera mensajes con long polling y, en cada vuelta, revisa los
rituales pendientes. Los errores del canal se reintentan con espera creciente; los de un mensaje
o de los rituales se registran y el servicio sigue. Códigos de salida: 0 correcto, 2 error de
configuración, 3 ya hay otro servicio en marcha.
"""

import argparse
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol, TextIO

from lifeos.bootstrap import STARTUP_ERRORS, Settings, build_service, telegram_channel
from lifeos.domain.errors import LifeOSError
from lifeos.domain.text import printable
from lifeos.ports.chat import ChatChannel, IncomingMessage
from lifeos.service.bot import OwnerBot

POLL_TIMEOUT_S = 25
DISCOVER_TIMEOUT_S = 30
BACKOFF_START_S = 5.0
BACKOFF_MAX_S = 300.0
NEWLINE = "\n"
EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_RUNNING = 3
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
STARTUP_OR_VALUE: tuple[type[Exception], ...] = (ValueError, *STARTUP_ERRORS)


class _Handler(Protocol):
    def handle(self, message: IncomingMessage) -> None: ...


class _Rituals(Protocol):
    def tick(self) -> object: ...


def _stderr(line: str) -> None:
    sys.stderr.write(f"{printable(line)}{NEWLINE}")
    sys.stderr.flush()


class ServiceLoop:
    def __init__(
        self,
        channel: ChatChannel,
        bot: _Handler,
        rituals: _Rituals,
        *,
        sleep: Callable[[float], None] = time.sleep,
        log: Callable[[str], None] = _stderr,
    ) -> None:
        self._channel = channel
        self._bot = bot
        self._rituals = rituals
        self._sleep = sleep
        self._log = log
        self._offset: int | None = None
        self._backoff = BACKOFF_START_S
        self._last_ritual_error: str | None = None

    def run(self, *, iterations: int | None = None) -> None:
        done = 0
        while iterations is None or done < iterations:
            self._receive()
            self._tick()
            done += 1

    def _receive(self) -> None:
        try:
            batch = self._channel.poll(self._offset, POLL_TIMEOUT_S)
        except LifeOSError as error:
            self._log(f"telegram: {error}; reintento en {self._backoff:.0f} s")
            self._sleep(self._backoff)
            self._backoff = min(self._backoff * 2, BACKOFF_MAX_S)
            return
        self._backoff = BACKOFF_START_S
        for message in batch:
            # El offset avanza antes de atender: un mensaje que falla no se repite sin fin.
            self._offset = message.update_id + 1
            try:
                self._bot.handle(message)
            except LifeOSError as error:
                self._log(f"mensaje {message.update_id}: {error}")

    def _tick(self) -> None:
        try:
            self._rituals.tick()
        except LifeOSError as error:
            text = str(error)
            if text != self._last_ritual_error:  # el mismo error en cada vuelta se avisa una vez
                self._log(f"rituales: {text}")
            self._last_ritual_error = text
            return
        self._last_ritual_error = None


def _discover(channel: ChatChannel, out: TextIO) -> int:
    print(
        f"Envía /start a tu bot desde Telegram. Espero {DISCOVER_TIMEOUT_S} s...",
        file=out,
        flush=True,
    )
    chats = sorted({m.chat_id for m in channel.poll(None, DISCOVER_TIMEOUT_S) if m.chat_id})
    if not chats:
        print("No llegó ningún mensaje. Vuelve a intentarlo.", file=out)
        return EXIT_OK
    for chat_id in chats:
        print(f"chat_id: {chat_id}", file=out)
    print("Pon el tuyo en TELEGRAM_OWNER_CHAT_ID dentro de .env.", file=out)
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path = DEFAULT_PROJECT_ROOT,
    out: TextIO | None = None,
) -> int:
    stdout = out or sys.stdout
    parser = argparse.ArgumentParser(
        prog="lifeos-servicio", description="Rituales programados y bot de Telegram (Fase 6)."
    )
    parser.add_argument(
        "--descubrir", action="store_true", help="mostrar el chat_id de quien escriba al bot"
    )
    parser.add_argument("--una-vez", action="store_true", help="una sola vuelta (diagnóstico)")
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env(project_root, environ)
        if args.descubrir:
            return _discover(telegram_channel(settings), stdout)
        service = build_service(settings)
    except STARTUP_OR_VALUE as error:
        _stderr(f"Error de configuración: {error}")
        return EXIT_CONFIG
    except LifeOSError as error:  # p. ej., Telegram no responde en --descubrir
        _stderr(f"Error: {error}")
        return EXIT_CONFIG
    bot = OwnerBot(
        service.channel,
        service.owner_chat_id,
        vault=service.vault,
        coach=service.coach,
        clock=service.runtime.clock,
        audit=service.runtime.audit_log,
        transcriber=service.transcriber,
    )
    try:
        with service.lock:
            print("lifeos-servicio en marcha. Ctrl+C para detener.", file=stdout, flush=True)
            ServiceLoop(service.channel, bot, service.rituals).run(
                iterations=1 if args.una_vez else None
            )
    except KeyboardInterrupt:  # pragma: no cover - parada manual
        return EXIT_OK
    except LifeOSError as error:
        _stderr(f"Error: {error}")
        return EXIT_RUNNING
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entrada manual
    sys.exit(main(sys.argv[1:]))
