"""Contrato `propuesta_area_v1` de los especialistas (F8-T02, SEC-07)."""

import json
from collections.abc import Mapping
from datetime import date

import pytest

from lifeos.adapters.schemas import SCHEMAS
from lifeos.domain.area_proposals import ProposalKind, ProposalStatus
from lifeos.domain.ids import TaskId
from lifeos.domain.task import Priority
from lifeos.ports.llm import ModelOutputError

pytestmark = pytest.mark.security

SCHEMA = SCHEMAS["propuesta_area_v1"]


def _raw(*items: Mapping[str, object]) -> str:
    return json.dumps({"propuestas": list(items)})


PRIORITY = {"tipo": "prioridad", "tarea": "t-abc234", "prioridad": "alta", "motivo": "vence"}
NEW = {
    "tipo": "nueva_tarea",
    "titulo": "Repasar capítulo 3",
    "esfuerzo": "90m",
    "vence": "2026-11-20",
    "prioridad": "media",
    "motivo": "meta m1",
}


def test_valid_output_becomes_pending_items_with_keys() -> None:
    items = SCHEMA.parse(_raw(PRIORITY, NEW))

    assert [i.key for i in items] == ["p1", "p2"]
    first, second = items
    assert first.kind is ProposalKind.PRIORIDAD
    assert first.task_id == TaskId("t-abc234")
    assert first.priority is Priority.ALTA
    assert second.title == "Repasar capítulo 3"
    assert second.effort is not None and second.effort.minutes == 90
    assert second.due == date(2026, 11, 20)
    assert all(i.status is ProposalStatus.PENDIENTE for i in items)


def test_empty_list_is_valid() -> None:
    assert SCHEMA.parse(_raw()) == ()


@pytest.mark.parametrize(
    "item",
    [
        {**PRIORITY, "estado": "aceptada"},  # el modelo no decide el estado
        {**PRIORITY, "tarea": "t-INVALID"},
        {**PRIORITY, "prioridad": "urgente"},
        {**PRIORITY, "tipo": "borrar"},
        {k: v for k, v in PRIORITY.items() if k != "tarea"},
        {**NEW, "esfuerzo": "mucho"},
        {**NEW, "vence": "2026-02-30"},
        {**NEW, "tarea": "t-abc234"},
        {**PRIORITY, "motivo": ""},
        {**PRIORITY, "bloques": []},
    ],
)
def test_invalid_items_are_rejected(item: dict[str, object]) -> None:
    with pytest.raises(ModelOutputError, match="propuesta_area_v1"):
        SCHEMA.parse(_raw(item))


def test_too_many_items_are_rejected() -> None:
    with pytest.raises(ModelOutputError):
        SCHEMA.parse(_raw(*([PRIORITY] * 21)))


def test_error_does_not_echo_model_text() -> None:
    with pytest.raises(ModelOutputError) as raised:
        SCHEMA.parse(_raw({**PRIORITY, "prioridad": "SECRETO-123"}))

    assert "SECRETO-123" not in str(raised.value)
