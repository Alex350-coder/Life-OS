"""Puerto de transcripción de notas de voz (ADR-0015)."""

from typing import Protocol

from lifeos.domain.errors import LifeOSError


class TranscriptionError(LifeOSError):
    """No se pudo transcribir el audio."""


class TranscriberUnavailableError(TranscriptionError):
    """La transcripción no está instalada (falta el extra `voz`)."""


class Transcriber(Protocol):
    def transcribe(self, audio: bytes) -> str: ...
