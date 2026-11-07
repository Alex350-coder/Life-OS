"""Cliente de la Bot API de Telegram sobre `urllib` (ADR-0014).

Garantías: solo `https` (o `http` hacia 127.0.0.1 en pruebas), sin redirecciones ni proxy en
pruebas locales, respuestas de 1 MiB como máximo y notas de voz de `MAX_VOICE_BYTES`. El token
va en la ruta de la URL, así que ningún error incluye la URL, el cuerpo recibido ni el token.
Los mensajes se envían como texto plano (sin `parse_mode`): los datos de la bóveda no se
interpretan como marcado.
"""

import http.client
import json
import re
import urllib.error
import urllib.request
from http.client import HTTPMessage
from typing import IO
from urllib.parse import urlsplit

from lifeos.adapters.secrets import Secret
from lifeos.ports.chat import ChatError, IncomingMessage, VoiceNote
from lifeos.ports.notify import NotifyError

DEFAULT_BASE_URL = "https://api.telegram.org"
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_VOICE_BYTES = 5 * 1024 * 1024
MAX_MESSAGE_CHARS = 4096
REQUEST_MARGIN_S = 10  # margen sobre el long polling antes de dar la conexión por perdida
SEND_TIMEOUT_S = 20
_CHUNK_BYTES = 64 * 1024
OFFICIAL_HOST = "api.telegram.org"
_LOOPBACK = "127.0.0.1"
_TOKEN_RE = re.compile(r"\d{1,20}:[A-Za-z0-9_-]{10,100}", re.ASCII)
_FILE_PATH_RE = re.compile(r"[A-Za-z0-9_-]{1,64}(/[A-Za-z0-9_.-]{1,128}){0,3}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> None:
        return None


def _check_base_url(base_url: str) -> str:
    # El token viaja en la ruta: solo puede ir al host oficial (o a 127.0.0.1 en pruebas).
    parts = urlsplit(base_url)
    official = parts.scheme == "https" and parts.hostname == OFFICIAL_HOST
    loopback = parts.scheme == "http" and parts.hostname == _LOOPBACK
    if not (official or loopback):
        raise ValueError(
            f"Telegram debe usar https://{OFFICIAL_HOST} (http solo hacia 127.0.0.1 en pruebas)"
        )
    return base_url.rstrip("/")


def _units(text: str) -> int:
    """Longitud según Telegram: unidades UTF-16 (un emoji puede contar 2)."""
    return len(text.encode("utf-16-le")) // 2


def _pieces(line: str) -> list[str]:
    """Una línea en trozos que caben en un mensaje, sin partir caracteres."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for char in line:
        width = _units(char)
        if size + width > MAX_MESSAGE_CHARS:
            pieces.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += width
    pieces.append("".join(current))
    return pieces


def _split(text: str) -> list[str]:
    """Mensajes de como mucho MAX_MESSAGE_CHARS, cortando por líneas cuando se puede. Un texto
    vacío no produce mensajes (Telegram rechaza los vacíos)."""
    if not text:
        return []
    chunks: list[str] = []
    current: str | None = None
    for line in text.split("\n"):
        first, *rest = _pieces(line)
        if current is not None and _units(current) + 1 + _units(first) <= MAX_MESSAGE_CHARS:
            current = f"{current}\n{first}"
        else:
            if current is not None:
                chunks.append(current)
            current = first
        for piece in rest:  # continuación de una línea larga: sin salto de línea añadido
            chunks.append(current)
            current = piece
    if current is not None:
        chunks.append(current)
    return chunks


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _voice(raw: object) -> VoiceNote | None:
    if not isinstance(raw, dict):
        return None
    file_id, duration = raw.get("file_id"), _int(raw.get("duration"))
    if not isinstance(file_id, str) or duration is None:
        raise ChatError("Telegram envió una nota de voz sin file_id o duración")
    return VoiceNote(file_id, duration, _int(raw.get("file_size")))


def _message(raw: object) -> IncomingMessage:
    if not isinstance(raw, dict) or _int(raw.get("update_id")) is None:
        # Sin update_id no se puede avanzar el offset: el lote entero es inválido.
        raise ChatError("Telegram envió una actualización sin update_id")
    update_id = raw["update_id"]
    message = raw.get("message")
    if not isinstance(message, dict):
        return IncomingMessage(update_id, None, None, None)
    try:
        voice = _voice(message.get("voice"))
    except ChatError:
        # Una nota mal formada no bloquea el lote: se devuelve vacía para que el offset avance.
        return IncomingMessage(update_id, None, None, None)
    chat = message.get("chat")
    chat_id = _int(chat.get("id")) if isinstance(chat, dict) else None
    text = message.get("text")
    return IncomingMessage(update_id, chat_id, text if isinstance(text, str) else None, voice)


class TelegramBotClient:
    """Implementación de `ChatChannel` para la Bot API."""

    def __init__(self, token: Secret, *, base_url: str = DEFAULT_BASE_URL) -> None:
        if not _TOKEN_RE.fullmatch(token.reveal()):
            raise ValueError("TELEGRAM_BOT_TOKEN no tiene el formato <número>:<clave>")
        self._base = _check_base_url(base_url)
        self._token = token
        handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
        if urlsplit(self._base).hostname == _LOOPBACK:
            handlers.append(urllib.request.ProxyHandler({}))
        self._opener = urllib.request.build_opener(*handlers)

    def __repr__(self) -> str:
        return f"TelegramBotClient(base_url={self._base!r}, token=***)"

    # --- HTTP -------------------------------------------------------------------------------

    def _open(self, request: urllib.request.Request, timeout_s: float, limit: int) -> bytes:
        try:
            with self._opener.open(request, timeout=timeout_s) as response:
                if response.status != 200:
                    raise ChatError(f"Telegram respondió {response.status}")
                return self._read(response, limit)
        except urllib.error.HTTPError as error:
            error.close()  # el cuerpo de error se descarta
            raise ChatError(f"Telegram respondió {error.code}") from None
        except urllib.error.URLError as error:
            name = type(error.reason).__name__
            raise ChatError(f"no se pudo conectar con Telegram ({name})") from None
        except (TimeoutError, http.client.HTTPException, OSError) as error:
            raise ChatError(
                f"conexión con Telegram interrumpida ({type(error).__name__})"
            ) from None

    @staticmethod
    def _read(response: http.client.HTTPResponse, limit: int) -> bytes:
        chunks: list[bytes] = []
        size = 0
        while chunk := response.read1(_CHUNK_BYTES):
            size += len(chunk)
            if size > limit:
                kind = "nota de voz demasiado grande" if limit == MAX_VOICE_BYTES else "respuesta"
                raise ChatError(f"{kind}: supera el tamaño de {limit} bytes")
            chunks.append(chunk)
        return b"".join(chunks)

    def _call(self, method: str, payload: dict[str, object], timeout_s: float) -> object:
        request = urllib.request.Request(  # noqa: S310 - URL validada en el constructor
            f"{self._base}/bot{self._token.reveal()}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        raw = self._open(request, timeout_s, MAX_RESPONSE_BYTES)
        try:
            data = json.loads(raw)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ChatError(f"Telegram no devolvió JSON válido en {method}") from None
        if not isinstance(data, dict):
            raise ChatError(f"Telegram devolvió una respuesta inesperada en {method}")
        if data.get("ok") is not True:
            raise ChatError(f"Telegram rechazó {method} (código {_int(data.get('error_code'))})")
        return data.get("result")

    # --- ChatChannel ------------------------------------------------------------------------

    def poll(self, offset: int | None, timeout_s: int) -> tuple[IncomingMessage, ...]:
        payload: dict[str, object] = {"timeout": timeout_s, "allowed_updates": ["message"]}
        if offset is not None:
            payload = {"offset": offset, **payload}
        result = self._call("getUpdates", payload, timeout_s + REQUEST_MARGIN_S)
        if not isinstance(result, list):
            raise ChatError("Telegram devolvió una lista de actualizaciones inválida")
        return tuple(_message(item) for item in result)

    def send(self, chat_id: int, text: str) -> None:
        for chunk in _split(text):
            self._call("sendMessage", {"chat_id": chat_id, "text": chunk}, SEND_TIMEOUT_S)

    def download_voice(self, voice: VoiceNote) -> bytes:
        if voice.size is not None and voice.size > MAX_VOICE_BYTES:
            raise ChatError(f"nota de voz demasiado grande: más de {MAX_VOICE_BYTES} bytes")
        result = self._call("getFile", {"file_id": voice.file_id}, SEND_TIMEOUT_S)
        file_path = result.get("file_path") if isinstance(result, dict) else None
        if not isinstance(file_path, str) or not _FILE_PATH_RE.fullmatch(file_path):
            raise ChatError("Telegram devolvió una ruta de archivo no válida")
        if ".." in file_path.split("/"):
            raise ChatError("Telegram devolvió una ruta de archivo no válida")
        request = urllib.request.Request(  # noqa: S310 - URL validada en el constructor
            f"{self._base}/file/bot{self._token.reveal()}/{file_path}", method="GET"
        )
        return self._open(request, SEND_TIMEOUT_S, MAX_VOICE_BYTES)


class TelegramNotifier:
    """`Notifier` que escribe al chat del propietario."""

    def __init__(self, client: TelegramBotClient, *, chat_id: int) -> None:
        self._client = client
        self._chat_id = chat_id

    def send(self, text: str) -> None:
        try:
            self._client.send(self._chat_id, text)
        except ChatError as error:
            raise NotifyError(str(error)) from None
