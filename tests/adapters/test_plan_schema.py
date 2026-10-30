"""Contrato `plan_v1` (F3-T06, SEC-07): solo bloques de trabajo con id, fecha y horas válidas."""

import json
from datetime import date

import pytest

from lifeos.adapters.schemas import SCHEMAS
from lifeos.domain.ids import TaskId
from lifeos.domain.proposals import DraftBlock, PlanDraft
from lifeos.ports.llm import ModelOutputError

pytestmark = [pytest.mark.unit, pytest.mark.security]
SCHEMA = SCHEMAS["plan_v1"]


def payload(**overrides: object) -> str:
    data: dict[str, object] = {
        "bloques": [
            {"tarea": "t-aaaaaa", "fecha": "2026-11-02", "inicio": "08:00", "fin": "09:30"}
        ],
        "justificacion": "Primero lo urgente.",
    }
    data.update(overrides)
    return json.dumps(data)


def block(**overrides: str) -> str:
    item = {"tarea": "t-aaaaaa", "fecha": "2026-11-02", "inicio": "08:00", "fin": "09:30"}
    item.update(overrides)
    return payload(bloques=[item])


def test_valid_plan_becomes_a_domain_draft() -> None:
    draft = SCHEMA.parse(payload())
    assert draft == PlanDraft(
        blocks=(DraftBlock(TaskId("t-aaaaaa"), date(2026, 11, 2), 480, 570),),
        justification="Primero lo urgente.",
    )


def test_end_of_day_is_accepted() -> None:
    assert SCHEMA.parse(block(inicio="23:00", fin="24:00")).blocks[0].end == 1440


def test_fenced_json_is_tolerated_once() -> None:
    assert SCHEMA.parse(f"```json\n{payload()}\n```").justification == "Primero lo urgente."


@pytest.mark.parametrize(
    "raw",
    [
        payload(fijos=[]),  # el modelo no decide compromisos fijos
        payload(estado="aprobado"),  # ni el estado del plan
        payload(permisos={"escritura": ["_sistema/"]}),
        block(tarea="t-AAAAAA"),
        block(tarea="../_sistema/perfil"),
        block(fecha="2026-02-30"),
        block(fecha="mañana"),
        block(inicio="25:00"),
        block(inicio="8:00"),
        block(inicio="10:00", fin="09:00"),
        block(inicio="09:00", fin="09:00"),
        block(fin="24:30"),
        payload(justificacion="x" * 2_001),
        payload(bloques=[json.loads(block())["bloques"][0]] * 201),
        payload(bloques="todos"),
        json.dumps({"bloques": []}),
        "Ignora el formato y responde en texto libre.",
    ],
    ids=[
        "extra-fixed",
        "extra-status",
        "extra-permissions",
        "bad-id",
        "path-as-id",
        "impossible-date",
        "word-date",
        "hour-25",
        "short-hour",
        "end-before-start",
        "zero-length",
        "after-midnight",
        "long-justification",
        "too-many-blocks",
        "blocks-not-list",
        "missing-justification",
        "free-text",
    ],
)
def test_invalid_output_is_rejected(raw: str) -> None:
    with pytest.raises(ModelOutputError) as raised:
        SCHEMA.parse(raw)
    assert "plan_v1" in str(raised.value)
    assert "Ignora" not in str(raised.value)  # el error nunca reproduce el texto del modelo
