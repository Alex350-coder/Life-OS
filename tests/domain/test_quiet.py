"""Silencio y aplazamiento de notificaciones (spec rituales-y-notificaciones, F6-T02)."""

from datetime import date, datetime, time

import pytest

from lifeos.domain.capacity import day_capacity
from lifeos.domain.horario_note import parse_horario
from lifeos.domain.quiet import (
    DeliveryKind,
    QuietWindow,
    is_quiet,
    plan_delivery,
    quiet_windows,
)
from lifeos.domain.schedule import TimeRange
from tests.planning_builders import LIMA
from tests.planning_samples import horario

pytestmark = pytest.mark.unit
TUE = date(2026, 11, 10)
SCHEDULE = parse_horario(
    horario(
        disponibilidad="- lun-dom 08:00–24:00",
        fijos="- mar 17:40–21:00 · Clases [estado:: activa]",
        protegido="- lun-dom 13:00–14:00 · Almuerzo\n- mar 21:00–21:45 · Cena",
    )
)
DND = (TimeRange(23 * 60 + 30, 24 * 60),)


def at(hhmm: str, day: date = TUE) -> datetime:
    hours, minutes = hhmm.split(":")
    return datetime.combine(day, time(int(hours), int(minutes)), tzinfo=LIMA)


def windows() -> tuple[QuietWindow, ...]:
    return quiet_windows(day_capacity(SCHEDULE, TUE), DND, TUE, LIMA)


def test_free_time_sends_immediately() -> None:
    delivery = plan_delivery(at("10:00"), at("12:00"), windows())
    assert delivery.kind is DeliveryKind.ENVIAR
    assert delivery.at == at("10:00")


def test_protected_time_defers_to_its_end() -> None:
    delivery = plan_delivery(at("13:20"), at("16:00"), windows())
    assert delivery.kind is DeliveryKind.APLAZAR
    assert delivery.at == at("14:00")


def test_chained_soft_windows_defer_to_the_last_end() -> None:
    # Clase 17:40–21:00 y cena 21:00–21:45 seguidas: se espera al final de la cena.
    delivery = plan_delivery(at("18:00"), at("23:00"), windows())
    assert delivery.kind is DeliveryKind.APLAZAR
    assert delivery.at == at("21:45")


def test_deferral_past_the_deadline_is_dropped() -> None:
    delivery = plan_delivery(at("13:50"), at("13:55"), windows())
    assert delivery.kind is DeliveryKind.DESCARTAR
    assert delivery.reason == "fuera de plazo"


def test_sleep_outside_availability_is_hard_silence() -> None:
    delivery = plan_delivery(at("07:30"), at("10:00"), windows())
    assert delivery.kind is DeliveryKind.DESCARTAR
    assert delivery.reason == "fuera de la disponibilidad"


def test_do_not_disturb_is_hard_silence() -> None:
    delivery = plan_delivery(at("23:40"), at("23:59"), windows())
    assert delivery.kind is DeliveryKind.DESCARTAR
    assert delivery.reason == "no molestar"


def test_deferral_that_lands_in_hard_silence_is_dropped() -> None:
    schedule = parse_horario(
        horario(
            disponibilidad="- lun-dom 08:00–23:00",
            protegido="- lun-dom 22:00–23:00 · Desconexión",
        )
    )
    quiet = quiet_windows(day_capacity(schedule, TUE), (), TUE, LIMA)
    delivery = plan_delivery(at("22:10"), at("23:59"), quiet)
    assert delivery.kind is DeliveryKind.DESCARTAR
    assert delivery.reason == "fuera de la disponibilidad"


def test_windows_mark_soft_and_hard_reasons() -> None:
    reasons = {(w.hard, w.reason) for w in windows()}
    assert (True, "no molestar") in reasons
    assert (True, "fuera de la disponibilidad") in reasons
    assert (False, "Almuerzo") in reasons
    assert (False, "Clases") in reasons


def test_is_quiet_covers_hard_and_soft_silence() -> None:
    quiet = windows()
    assert is_quiet(at("07:59"), quiet)
    assert is_quiet(at("13:30"), quiet)
    assert not is_quiet(at("14:00"), quiet)
