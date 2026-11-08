"""`_sistema/rituales.md`: formato, errores y ocurrencias (spec de rituales, F6-T01)."""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from lifeos.domain.rituals import (
    GRACE_MINUTES,
    RitualesError,
    RitualKind,
    parse_rituales,
    reminder_occurrences,
    ritual_occurrences,
)
from lifeos.domain.schedule import TimeRange, Weekday
from tests.planning_builders import work

pytestmark = pytest.mark.unit
LIMA = ZoneInfo("America/Lima")
MON = date(2026, 11, 9)
SUN = date(2026, 11, 15)

RITUALES = """---
tipo: rituales
---

# Rituales

## Rituales
- briefing · lun-vie 11:00
- briefing · sáb,dom 09:00
- cierre · lun,mié 16:45
- propuesta_semanal · dom 17:00
- recordatorio · 5m antes

## No molestar
- lun-dom 23:30–24:00
<!-- comentario ignorado -->
"""


def at(day: date, hhmm: str) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime.combine(day, time(int(hours), int(minutes)), tzinfo=LIMA)


def test_parses_timed_rituals_reminder_lead_and_quiet_time() -> None:
    config = parse_rituales(RITUALES)
    kinds = [(r.kind, r.minute) for r in config.timed_for(MON)]
    assert kinds == [(RitualKind.BRIEFING, 11 * 60), (RitualKind.CIERRE, 16 * 60 + 45)]
    assert [r.kind for r in config.timed_for(SUN)] == [
        RitualKind.BRIEFING,
        RitualKind.PROPUESTA_SEMANAL,
    ]
    assert config.reminder_lead == 5
    assert config.quiet_for(MON) == (TimeRange(23 * 60 + 30, 24 * 60),)


def test_reminders_can_be_disabled() -> None:
    config = parse_rituales(RITUALES.replace("5m antes", "desactivado"))
    assert config.reminder_lead is None


def test_quiet_section_is_optional() -> None:
    text = "## Rituales\n- briefing · lun 08:00\n"
    config = parse_rituales(text)
    assert config.quiet_for(MON) == ()
    assert config.reminder_lead is None


@pytest.mark.parametrize(
    ("line", "fragment"),
    [
        ("- briefing lun 08:00", "línea 2"),  # falta el separador
        ("- siesta · lun 08:00", "ritual desconocido"),
        ("- briefing · lun 25:00", "hora"),
        ("- briefing · xyz 08:00", "día"),
        ("- recordatorio · 0m antes", "antelación"),
        ("- recordatorio · 3h antes", "antelación"),
    ],
)
def test_malformed_lines_are_reported_with_their_number(line: str, fragment: str) -> None:
    with pytest.raises(RitualesError) as error:
        parse_rituales(f"## Rituales\n{line}\n")
    assert fragment in str(error.value)


def test_all_problems_are_reported_together() -> None:
    text = "## Rituales\n- siesta · lun 08:00\n- briefing · xyz 08:00\n"
    with pytest.raises(RitualesError) as error:
        parse_rituales(text)
    assert len(error.value.problems) == 2


def test_a_ritual_cannot_repeat_a_day() -> None:
    text = "## Rituales\n- briefing · lun-vie 08:00\n- briefing · vie 09:00\n"
    with pytest.raises(RitualesError, match="repite"):
        parse_rituales(text)


def test_reminder_is_declared_once() -> None:
    text = "## Rituales\n- recordatorio · 5m antes\n- recordatorio · 10m antes\n"
    with pytest.raises(RitualesError, match="recordatorio"):
        parse_rituales(text)


def test_quiet_ranges_cannot_cross_midnight() -> None:
    with pytest.raises(RitualesError):
        parse_rituales("## No molestar\n- lun-dom 23:00–07:00\n")


def test_text_outside_the_sections_is_ignored() -> None:
    text = "Notas libres: briefing · lun 99:99\n\n## Otra\n- lo que sea\n\n## Rituales\n"
    assert parse_rituales(text).timed_for(MON) == ()


def test_days_accept_ranges_and_lists() -> None:
    config = parse_rituales("## Rituales\n- cierre · lun,mié,vie 22:00\n")
    (rule,) = config.timed
    assert rule.days == frozenset({Weekday.LUN, Weekday.MIE, Weekday.VIE})


# --- Ocurrencias ------------------------------------------------------------------------------


def test_occurrences_have_stable_keys_and_grace_deadlines() -> None:
    config = parse_rituales(RITUALES)
    briefing, closing = ritual_occurrences(config, MON, LIMA)
    assert briefing.key == "briefing:2026-11-09"
    assert briefing.at == at(MON, "11:00")
    assert briefing.deadline == briefing.at + timedelta(minutes=GRACE_MINUTES[RitualKind.BRIEFING])
    assert closing.key == "cierre:2026-11-09"


def test_reminders_fire_before_each_work_block_and_expire_at_its_start() -> None:
    config = parse_rituales(RITUALES)
    blocks = (
        work(MON, "11:00–12:00", "t-aaaaaa", "Guitarra"),
        work(MON, "14:00–15:30", "t-bbbbbb", "Dibujo"),
    )
    first, second = reminder_occurrences(config, MON, blocks)
    assert first.key == "recordatorio:2026-11-09:t-aaaaaa:11:00"
    assert first.at == at(MON, "10:55")
    assert first.deadline == at(MON, "11:00")
    assert first.block == blocks[0]
    assert second.at == at(MON, "13:55")


def test_no_reminders_when_disabled() -> None:
    config = parse_rituales(RITUALES.replace("5m antes", "desactivado"))
    assert reminder_occurrences(config, MON, (work(MON, "11:00–12:00", "t-aaaaaa"),)) == ()
