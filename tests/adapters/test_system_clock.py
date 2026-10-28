"""Reloj real: hora con zona, tiempo monotónico creciente y espera."""

from datetime import timedelta, timezone

import pytest

from lifeos.adapters.system_clock import SystemClock

pytestmark = [pytest.mark.unit]


def test_system_clock_returns_aware_times_in_its_zone() -> None:
    zone = timezone(timedelta(hours=-5))
    now = SystemClock(zone).now()
    assert now.utcoffset() == timedelta(hours=-5)


def test_system_clock_monotonic_advances_after_sleep() -> None:
    clock = SystemClock()
    before = clock.monotonic()
    clock.sleep(0.05)  # el reloj monotónico de Windows avanza en pasos de ~15 ms
    assert clock.monotonic() > before
