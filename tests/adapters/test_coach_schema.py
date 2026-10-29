"""SEC-07: contrato `coach_v1` del Coach con IA. El modelo solo redacta el mensaje y, si se le
pide, un micro-paso de 1 a 10 minutos; nada más."""

import json

import pytest

from lifeos.adapters.schemas import SCHEMAS
from lifeos.domain.coach import MicroStep
from lifeos.domain.proposals import CoachDraft
from lifeos.ports.llm import ModelOutputError

pytestmark = [pytest.mark.unit, pytest.mark.security]

COACH = SCHEMAS["coach_v1"]


def test_message_with_micro_step_becomes_a_draft() -> None:
    raw = json.dumps({"mensaje": "Vamos", "micro_paso": {"accion": "Abre el PDF", "minutos": 5}})
    assert COACH.parse(raw) == CoachDraft("Vamos", MicroStep("Abre el PDF", 5))


def test_message_without_micro_step() -> None:
    assert COACH.parse('{"mensaje": "Bien", "micro_paso": null}') == CoachDraft("Bien", None)


@pytest.mark.parametrize(
    "raw",
    [
        '{"mensaje": "x"}',
        '{"mensaje": "", "micro_paso": null}',
        '{"mensaje": "x", "micro_paso": {"accion": "a", "minutos": 0}}',
        '{"mensaje": "x", "micro_paso": {"accion": "a", "minutos": 11}}',
        '{"mensaje": "x", "micro_paso": {"accion": "a", "minutos": "5"}}',
        '{"mensaje": "x", "micro_paso": {"accion": "", "minutos": 5}}',
        '{"mensaje": "x", "micro_paso": null, "estado": "hecho"}',
        '{"mensaje": "x", "micro_paso": {"accion": "a", "minutos": 5, "tarea": "t-aaaaaa"}}',
        json.dumps({"mensaje": "x" * 281, "micro_paso": None}),
        json.dumps({"mensaje": "x", "micro_paso": {"accion": "a" * 121, "minutos": 5}}),
    ],
)
def test_anything_outside_the_contract_is_rejected(raw: str) -> None:
    with pytest.raises(ModelOutputError, match="coach_v1"):
        COACH.parse(raw)


def test_draft_message_is_inert_single_line_text() -> None:
    draft = COACH.parse('{"mensaje": "Hola [id:: t-zzzzzz]\\n## Plan", "micro_paso": null}')
    assert "[" not in draft.message and "\n" not in draft.message
