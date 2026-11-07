"""Adaptadores del servicio: transcripción, estado de rituales y candado (ADR-0014, ADR-0015)."""

import sys
import types
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import pytest

from lifeos.adapters.json_state import JsonRitualState
from lifeos.adapters.process_lock import AlreadyRunningError, ProcessLock
from lifeos.adapters.whisper import FasterWhisperTranscriber
from lifeos.ports.state import RitualMark, RitualStatus, StateError
from lifeos.ports.transcribe import TranscriberUnavailableError, TranscriptionError

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 9, 14, 0, tzinfo=UTC)


# --- Transcripción (faster-whisper simulado) --------------------------------------------------


@dataclass
class FakeSegment:
    text: str


class FakeModel:
    instances: list["FakeModel"] = []  # noqa: RUF012 - registro de la prueba

    def __init__(self, size: str, *, device: str, compute_type: str) -> None:
        self.args = (size, device, compute_type)
        self.calls: list[dict[str, object]] = []
        FakeModel.instances.append(self)

    def transcribe(
        self, audio: BinaryIO, **options: object
    ) -> tuple[Iterator[FakeSegment], object]:
        self.calls.append({"audio": audio.read(), **options})
        return iter([FakeSegment(" Comprar cuerdas "), FakeSegment("para la guitarra.")]), None


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> type[FakeModel]:
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = FakeModel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    FakeModel.instances = []
    return FakeModel


def test_transcribes_from_memory_in_spanish_and_reuses_the_model(
    fake_whisper: type[FakeModel],
) -> None:
    transcriber = FasterWhisperTranscriber("small")
    assert transcriber.transcribe(b"audio-1") == "Comprar cuerdas para la guitarra."
    transcriber.transcribe(b"audio-2")
    (model,) = fake_whisper.instances
    assert model.args == ("small", "cpu", "int8")
    assert model.calls[0]["audio"] == b"audio-1"
    assert model.calls[0]["language"] == "es"
    assert model.calls[0]["vad_filter"] is True


def test_missing_extra_is_reported_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "faster_whisper", None)  # import falla
    with pytest.raises(TranscriberUnavailableError, match="voz"):
        FasterWhisperTranscriber("small").transcribe(b"audio")


def test_decoder_failures_become_transcription_errors(
    fake_whisper: type[FakeModel], monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: FakeModel, audio: BinaryIO, **options: object) -> None:
        raise RuntimeError("Invalid data found when processing input")

    monkeypatch.setattr(FakeModel, "transcribe", broken)
    with pytest.raises(TranscriptionError, match="RuntimeError"):
        FasterWhisperTranscriber("small").transcribe(b"no-audio")


def test_unknown_model_size_is_rejected() -> None:
    with pytest.raises(ValueError, match="modelo"):
        FasterWhisperTranscriber("gigante")


# --- Estado de rituales -----------------------------------------------------------------------


def test_state_round_trips_and_starts_empty(tmp_path: Path) -> None:
    store = JsonRitualState(tmp_path / "state" / "rituales.json")
    assert store.load() == {}
    marks = {
        "briefing:2026-11-09": RitualMark(RitualStatus.HECHO, 1, NOW),
        "cierre:2026-11-09": RitualMark(RitualStatus.FALLIDO, 2, NOW, "NotifyError: caído"),
    }
    store.save(marks)
    assert store.load() == marks


@pytest.mark.parametrize(
    "content",
    [
        "no es json",
        '{"k": {"status": "raro", "attempts": 1, "updated": "2026-11-09T14:00:00+00:00"}}',
        '{"k": {"status": "hecho", "attempts": -1, "updated": "2026-11-09T14:00:00+00:00"}}',
        '{"k": {"status": "hecho", "attempts": 1, "updated": "2026-11-09T14:00:00"}}',
        "[]",
    ],
)
def test_corrupt_state_is_an_explicit_error(tmp_path: Path, content: str) -> None:
    path = tmp_path / "rituales.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(StateError, match=r"rituales\.json"):
        JsonRitualState(path).load()


def test_save_replaces_the_file_atomically(tmp_path: Path) -> None:
    path = tmp_path / "rituales.json"
    store = JsonRitualState(path)
    store.save({"a": RitualMark(RitualStatus.HECHO, 1, NOW)})
    store.save({"b": RitualMark(RitualStatus.OMITIDO, 0, NOW)})
    assert set(store.load()) == {"b"}
    assert [p.name for p in tmp_path.iterdir()] == ["rituales.json"]


# --- Candado de instancia única ---------------------------------------------------------------


def test_a_second_instance_cannot_take_the_lock(tmp_path: Path) -> None:
    path = tmp_path / "servicio.lock"
    with ProcessLock(path), pytest.raises(AlreadyRunningError):
        ProcessLock(path).acquire()
    with ProcessLock(path):  # liberado al salir
        pass
