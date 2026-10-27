"""SEC-07: la salida del modelo se valida con un esquema estricto y se convierte a tipos propios."""

from datetime import time

import pytest
from lifeos.adapters.schemas import MAX_OUTPUT_CHARS, SCHEMAS
from lifeos.domain.proposals import CaptureDraft

from lifeos.domain.checkin import CheckInEntry
from lifeos.ports.llm import ModelOutputError

pytestmark = [pytest.mark.unit, pytest.mark.security]

CHECKIN = SCHEMAS["checkin_v1"]
CAPTURE = SCHEMAS["captura_v1"]


def test_registered_schema_ids() -> None:
    assert set(SCHEMAS) == {"captura_v1", "checkin_v1"}
    assert CHECKIN.id == "checkin_v1"


def test_valid_outputs_become_domain_objects() -> None:
    assert CHECKIN.parse('{"hora": "09:30", "texto": "Terminé"}') == CheckInEntry(
        time(9, 30), "Terminé"
    )
    assert CAPTURE.parse('{"texto": "Comprar pilas"}') == CaptureDraft("Comprar pilas")


def test_a_single_json_code_fence_is_tolerated() -> None:
    raw = '```json\n{"texto": "Comprar pilas"}\n```'
    assert CAPTURE.parse(raw) == CaptureDraft("Comprar pilas")
    assert CAPTURE.parse('```\n{"texto": "x"}\n```') == CaptureDraft("x")


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "no es json",
        'Claro, aquí está: {"texto": "x"}',
        '{"texto": "x"} y algo más',
        "[]",
        '"texto"',
        "null",
        '{"texto": "x", "extra": 1}',
        '{"texto": 5}',
        '{"texto": ""}',
        '{"texto": "a", "texto": "b"}',
        '{"texto": NaN}',
        '```json\n```json\n{"texto": "x"}\n```\n```',
        '{"Texto": "x"}',
    ],
)
def test_malformed_capture_outputs_are_rejected(raw: str) -> None:
    with pytest.raises(ModelOutputError):
        CAPTURE.parse(raw)


@pytest.mark.parametrize(
    "raw",
    [
        '{"hora": 930, "texto": "x"}',
        '{"hora": "9:30", "texto": "x"}',
        '{"hora": "25:00", "texto": "x"}',
        '{"hora": "09:30"}',
        '{"hora": "09:30", "texto": "fijar [avance:: 100]"}',
        '{"hora": "09:30", "texto": "dos\\nlíneas"}',
        '{"hora": "09:30", "texto": "x", "completada": true}',
    ],
)
def test_malformed_checkin_outputs_are_rejected(raw: str) -> None:
    with pytest.raises(ModelOutputError):
        CHECKIN.parse(raw)


def test_oversized_output_is_rejected_before_parsing() -> None:
    with pytest.raises(ModelOutputError, match="tamaño"):
        CAPTURE.parse('{"texto": "' + "x" * MAX_OUTPUT_CHARS + '"}')


def test_errors_do_not_echo_model_text() -> None:
    with pytest.raises(ModelOutputError) as info:
        CHECKIN.parse('{"hora": "09:30", "texto": "dato-sensible [avance:: 1]"}')
    assert "dato-sensible" not in str(info.value)
