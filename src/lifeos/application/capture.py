"""Captura rápida en 00-Inbox/ (F1-T05). El texto se guarda como dato, nunca se interpreta."""

import re
from datetime import datetime

from lifeos.application.tasks import ApplicationError
from lifeos.domain.errors import ValidationError
from lifeos.ports.vault import NoteExistsError, VaultPath, VaultStore

INBOX = VaultPath("00-Inbox")
MAX_CAPTURE_CHARS = 20_000
MAX_NAME_ATTEMPTS = 50
DEFAULT_SOURCE = "manual"
_SOURCE_RE = re.compile(r"[a-z0-9_-]{1,32}")


def _validate(text: str, source: str, now: datetime) -> None:
    if not text.strip():
        raise ValidationError("captura", text, "no puede estar vacía")
    if len(text) > MAX_CAPTURE_CHARS:
        raise ValidationError("captura", len(text), f"máximo {MAX_CAPTURE_CHARS} caracteres")
    if not _SOURCE_RE.fullmatch(source):
        raise ValidationError("fuente", source, "solo minúsculas, dígitos, '-' o '_'")
    if now.utcoffset() is None:
        raise ValidationError("fecha", now, "requiere zona horaria")


def _render(text: str, source: str, now: datetime) -> str:
    # Fuente y momento quedan registrados (spec memoria-y-vigencia).
    header = f"---\ncapturado: {now.isoformat()}\nfuente: {source}\n---\n\n"
    return header + text.rstrip("\r\n") + "\n"


def capture(
    vault: VaultStore, text: str, *, now: datetime, source: str = DEFAULT_SOURCE
) -> VaultPath:
    """Crea una nota nueva en el Inbox. Nunca sobrescribe: ante colisión usa un sufijo."""
    _validate(text, source, now)
    content = _render(text, source, now)
    stamp = now.strftime("%Y%m%d-%H%M%S")
    for attempt in range(1, MAX_NAME_ATTEMPTS + 1):
        name = stamp if attempt == 1 else f"{stamp}-{attempt}"
        path = INBOX.join(VaultPath(f"{name}.md"))
        try:
            vault.write(path, content, expected=None)
        except NoteExistsError:
            continue
        return path
    raise ApplicationError(f"demasiadas capturas en el mismo segundo ({stamp})")
