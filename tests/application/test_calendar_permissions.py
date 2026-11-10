"""Herramientas del calendario en la política (ADR-0017, SEC-04, SEC-13).

Leer la disponibilidad y publicar en el calendario son herramientas distintas. Publicar es
publicar el plan: solo el dueño del plan global puede tenerla (ADR-0002).
"""

import pytest

from lifeos.adapters.yaml_config import parse_agent, parse_catalog, parse_policy
from lifeos.application.permissions import (
    CALENDAR_READ_TOOL,
    CALENDAR_WRITE_TOOL,
    TOOL_WRITE_MODES,
    resolve_effective,
)
from lifeos.ports.config import ConfigRejectedError
from tests import config_samples as samples

pytestmark = [pytest.mark.unit, pytest.mark.security]


def _resolve(policy: dict, jefe: dict, coach: dict):  # type: ignore[no-untyped-def,type-arg]
    return resolve_effective(
        parse_policy(samples.dump(policy).encode()),
        parse_catalog(samples.dump(samples.catalog()).encode()),
        (parse_agent(samples.dump(jefe).encode()), parse_agent(samples.dump(coach).encode())),
        tools=TOOL_WRITE_MODES,
        contracts=samples.KNOWN_CONTRACTS,
    )


def test_calendar_tools_are_known_and_never_write_the_vault() -> None:
    assert TOOL_WRITE_MODES[CALENDAR_READ_TOOL] is None
    assert TOOL_WRITE_MODES[CALENDAR_WRITE_TOOL] is None


def test_jefe_can_receive_both_calendar_tools() -> None:
    effective = samples.effective()
    assert {CALENDAR_READ_TOOL, CALENDAR_WRITE_TOOL} <= effective["jefe"].tools
    assert not {CALENDAR_READ_TOOL, CALENDAR_WRITE_TOOL} & effective["coach"].tools


def test_policy_cannot_give_calendar_publishing_to_another_agent() -> None:
    policy = samples.policy()
    policy["agentes"]["coach"]["herramientas"].append(CALENDAR_WRITE_TOOL)
    with pytest.raises(ConfigRejectedError) as error:
        _resolve(policy, samples.jefe(), samples.coach())
    assert any(
        "solo el dueño del plan global" in problem and CALENDAR_WRITE_TOOL in problem
        for problem in error.value.violations
    )


def test_agent_cannot_request_a_calendar_tool_the_policy_does_not_grant() -> None:
    coach = samples.coach()
    coach["herramientas"].append(CALENDAR_READ_TOOL)
    with pytest.raises(ConfigRejectedError):
        _resolve(samples.policy(), samples.jefe(), coach)
