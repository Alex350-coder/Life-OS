"""Transcripción local con faster-whisper (ADR-0015). Dependencia opcional: extra `voz`.

El audio llega en memoria y se transcribe desde ella: nunca se escribe en disco. El modelo se
carga la primera vez que se usa y se reutiliza.
"""

import io
from typing import Any

from lifeos.ports.transcribe import TranscriberUnavailableError, TranscriptionError

# Revisión fijada de cada modelo en Hugging Face (Systran/faster-whisper-*): una subida nueva del
# modelo no cambia la transcripción sin revisarla (DT-08). Comprobadas el 2026-11-09.
MODEL_REVISIONS = {
    "tiny": "d90ca5fe260221311c53c58e660288d3deb8d356",
    "base": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
    "small": "536b0662742c02347bc0e980a01041f333bce120",
    "medium": "08e178d48790749d25932bbc082711ddcfdfbc4f",
}
MODEL_SIZES = frozenset(MODEL_REVISIONS)
LANGUAGE = "es"
MAX_AUDIO_S = 5 * 60


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
            try:
                self._model = WhisperModel(
                    self._size,
                    device="cpu",
                    compute_type="int8",
                    revision=MODEL_REVISIONS[self._size],
                )
            except Exception as error:  # descarga del modelo, DLL de ctranslate2, disco...
                raise TranscriptionError(
                    f"no se pudo cargar el modelo de voz ({type(error).__name__})"
                ) from None
        return self._model

    def transcribe(self, audio: bytes) -> str:
        model = self._load()
        try:
            # Los segmentos son perezosos: la duración real se conoce antes de transcribir.
            segments, info = model.transcribe(io.BytesIO(audio), language=LANGUAGE, vad_filter=True)
            if float(getattr(info, "duration", 0.0)) > MAX_AUDIO_S:
                raise TranscriptionError("la nota de voz dura más de 5 minutos")
            parts = [segment.text.strip() for segment in segments]
        except TranscriptionError:
            raise
        except Exception as error:
            raise TranscriptionError(
                f"no se pudo transcribir el audio ({type(error).__name__})"
            ) from None
        return " ".join(part for part in parts if part)
