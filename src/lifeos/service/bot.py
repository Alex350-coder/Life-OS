"""Bot del propietario en Telegram (F6-T03, F6-T04; ADR-0014, ADR-0015, SEC-12).

Solo atiende el `chat_id` configurado: lo demás se ignora sin responder y se audita con una
huella del chat, nunca con su contenido. El texto recibido es un dato: se guarda en el Inbox o
se compara con las órdenes conocidas, nunca se interpreta como instrucción. El bot no aprueba
planes ni cambia tareas.
"""

import hashlib
import hmac
import uuid
from collections.abc import Callable
from concurrent.futures import Future
from datetime import datetime, timedelta
from typing import Protocol

from lifeos.application import ritual_messages as messages
from lifeos.application.capture import capture
from lifeos.application.coach import CoachSession, NoDailyPlanError
from lifeos.domain.errors import LifeOSError, ValidationError
from lifeos.ports.chat import ChatChannel, IncomingMessage, VoiceNote
from lifeos.ports.clock import Clock
from lifeos.ports.logs import AuditLog, AuditRecord, AuditResult
from lifeos.ports.transcribe import Transcriber, TranscriberUnavailableError
from lifeos.ports.vault import VaultStore

MAX_VOICE_SECONDS = 300
TEXT_SOURCE = "telegram"
VOICE_SOURCE = "telegram-voz"
PREVIEW_CHARS = 500
REJECTION_AUDIT_EVERY = timedelta(hours=1)
MAX_TRACKED_CHATS = 1000
HELP = (
    "Life OS\n"
    "• Escribe cualquier cosa y la guardo en tu Inbox.\n"
    "• Envía una nota de voz y la transcribo (queda sin confirmar).\n"
    "• /hoy: el plan de hoy.\n"
    "• /captura <texto>: guardar en el Inbox.\n"
    "• /ayuda: esta ayuda."
)
_CAPTURE_COMMAND = "/captura"
_NOTHING_SAVED = "no guardé nada"


class VoiceWorker(Protocol):
    """Ejecuta la transcripción fuera del hilo principal (`ThreadPoolExecutor` de un hilo)."""

    def submit(self, fn: Callable[[bytes], str], audio: bytes, /) -> Future[str]: ...


def chat_fingerprint(chat_id: int, key: bytes) -> str:
    """Huella del chat para la auditoría: agrupa intentos sin guardar el id. Lleva clave (HMAC):
    un hash simple de un número de ~10 cifras se invertiría por fuerza bruta en segundos."""
    return hmac.new(key, str(chat_id).encode("ascii"), hashlib.sha256).hexdigest()[:16]


class OwnerBot:
    def __init__(
        self,
        channel: ChatChannel,
        owner_chat_id: int,
        *,
        vault: VaultStore,
        coach: CoachSession,
        clock: Clock,
        audit: AuditLog,
        transcriber: Transcriber | None,
        fingerprint_key: bytes,
        voice_worker: VoiceWorker | None = None,
    ) -> None:
        if not fingerprint_key:
            raise ValueError("la huella de SEC-12 necesita una clave")
        self._channel = channel
        self._owner = owner_chat_id
        self._vault = vault
        self._coach = coach
        self._clock = clock
        self._audit = audit
        self._transcriber = transcriber
        self._key = fingerprint_key
        self._last_rejection: dict[str, datetime] = {}
        self._worker = voice_worker
        self._pending: list[Future[str]] = []

    def handle(self, message: IncomingMessage) -> None:
        if message.chat_id is None:
            return  # no era un mensaje (p. ej., una edición): nada que atender
        if message.chat_id != self._owner:
            self._reject(message.chat_id)
            return
        if message.voice is not None:
            self._reply(self._voice(message.voice))
        elif message.text is not None:
            self._reply(self._text(message.text))
        else:
            self._reply("Por ahora solo entiendo texto o notas de voz.")

    # --- SEC-12 ---------------------------------------------------------------------------------

    def _reject(self, chat_id: int) -> None:
        # Un registro por chat ajeno y hora: el spam de un desconocido no llena la auditoría.
        fingerprint = chat_fingerprint(chat_id, self._key)
        now = self._clock.now()
        last = self._last_rejection.get(fingerprint)
        if last is not None and now - last < REJECTION_AUDIT_EVERY:
            return
        if len(self._last_rejection) >= MAX_TRACKED_CHATS:
            self._last_rejection.clear()
        self._last_rejection[fingerprint] = now
        self._audit.append(
            AuditRecord(
                ts=now,
                correlation_id=uuid.uuid4().hex,
                actor="telegram",
                action="mensaje_ajeno",
                target=f"chat:{fingerprint}",
                result=AuditResult.RECHAZADO,
                reason="chat_id ajeno (SEC-12)",
            )
        )

    def _reply(self, text: str) -> None:
        self._channel.send(self._owner, text)

    # --- Texto ----------------------------------------------------------------------------------

    def _text(self, text: str) -> str:
        stripped = text.strip()
        if not stripped.startswith("/"):
            return self._capture(text, source=TEXT_SOURCE)
        command, _, rest = stripped.partition(" ")
        command = command.split("@", 1)[0].casefold()  # /hoy@MiBot en grupos
        if command == "/hoy":
            return self._today()
        if command == _CAPTURE_COMMAND:
            if not rest.strip():
                return f"Uso: {_CAPTURE_COMMAND} <texto>. También puedes escribir sin la orden."
            return self._capture(rest.strip(), source=TEXT_SOURCE)
        return HELP

    def _today(self) -> str:
        try:
            return messages.briefing(self._coach.briefing())
        except NoDailyPlanError:
            return messages.no_plan(self._coach.today())
        except LifeOSError as error:  # horario o nota del día ilegibles: se responde igual
            return f"No pude leer el plan: {error}"

    def _capture(self, text: str, *, source: str, unconfirmed: bool = False) -> str:
        try:
            path = capture(
                self._vault, text, now=self._clock.now(), source=source, unconfirmed=unconfirmed
            )
        except ValidationError as error:
            return f"No pude guardarlo: {error.reason}."
        except LifeOSError as error:  # bóveda bloqueada, disco lleno...: el propietario lo sabe
            return f"No pude guardarlo ({error}); vuelve a enviarlo más tarde."
        state = " sin confirmar" if unconfirmed else ""
        return f"Guardado{state} en {path}."

    # --- Voz ------------------------------------------------------------------------------------

    def _voice(self, voice: VoiceNote) -> str:
        if self._transcriber is None:
            return f"La transcripción de voz no está configurada; {_NOTHING_SAVED}."
        if voice.duration_s > MAX_VOICE_SECONDS:
            return (
                f"La nota dura más de {MAX_VOICE_SECONDS // 60} minutos; {_NOTHING_SAVED}. "
                "Envíala en partes más cortas."
            )
        try:
            audio = self._channel.download_voice(voice)
            if self._worker is not None:
                # DT-04: solo la transcripción va a otro hilo; la captura y la respuesta vuelven
                # al hilo principal en `drain`, así la bóveda y Telegram no se tocan en paralelo.
                self._pending.append(self._worker.submit(self._transcriber.transcribe, audio))
                return "Nota de voz recibida; la estoy transcribiendo y te aviso al terminar."
            text = self._transcriber.transcribe(audio)
        except LifeOSError as error:
            return self._voice_error(error)
        return self._voice_result(text)

    @property
    def busy(self) -> bool:
        return bool(self._pending)

    def drain(self) -> None:
        """Atiende las transcripciones terminadas, en el orden en que llegaron."""
        while self._pending and self._pending[0].done():
            future = self._pending.pop(0)
            error = future.exception()
            if isinstance(error, LifeOSError):
                self._reply(self._voice_error(error))
            elif error is not None:
                self._reply(
                    f"No pude transcribir la nota ({type(error).__name__}); {_NOTHING_SAVED}."
                )
            else:
                self._reply(self._voice_result(future.result()))

    @staticmethod
    def _voice_error(error: LifeOSError) -> str:
        if isinstance(error, TranscriberUnavailableError):
            return f"La voz no está disponible (instala el extra 'voz'); {_NOTHING_SAVED}."
        return f"No pude transcribir la nota ({error}); {_NOTHING_SAVED}."

    def _voice_result(self, raw: str) -> str:
        text = raw.strip()
        if not text:
            return f"No entendí la nota de voz; {_NOTHING_SAVED}."
        saved = self._capture(text, source=VOICE_SOURCE, unconfirmed=True)
        preview = text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"
        return f"{saved}\nTranscripción (revísala): {preview}"
