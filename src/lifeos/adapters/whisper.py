"""Transcripción local con faster-whisper (ADR-0015). Dependencia opcional: extra `voz`.

El audio llega en memoria y se transcribe desde ella: nunca se escribe en disco. El modelo se
carga la primera vez que se usa y se reutiliza.
"""

import io
from typing import Any

from lifeos.ports.transcribe import TranscriberUnavailableError, TranscriptionError

MODEL_SIZES = frozenset({"tiny", "base", "small", "medium"})
LANGUAGE = "es"


class FasterWhisperTranscriber:
    def __init__(self, model_size: str = "small") -> None:
        if model_size not in MODEL_SIZES:
            raise ValueError(f"modelo de Whisper desconocido: {model_size}")
        self._size = model_size
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError:
                raise TranscriberUnavailableError(
                    "la transcripción no está instalada: ejecuta 'uv sync --extra voz'"
                ) from None
            self._model = WhisperModel(self._size, device="cpu", compute_type="int8")
        return self._model

    def transcribe(self, audio: bytes) -> str:
        model = self._load()
        try:
            segments, _info = model.transcribe(
                io.BytesIO(audio), language=LANGUAGE, vad_filter=True
            )
            parts = [segment.text.strip() for segment in segments]
        except Exception as error:
            raise TranscriptionError(
                f"no se pudo transcribir el audio ({type(error).__name__})"
            ) from None
        return " ".join(part for part in parts if part)
