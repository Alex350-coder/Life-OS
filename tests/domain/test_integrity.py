"""Integridad de IDs: los duplicados se reportan con todas sus ubicaciones y no se resuelven solos."""

import pytest

from lifeos.domain.ids import TaskId
from lifeos.domain.integrity import DuplicateTaskIdError, Location, find_duplicate_ids

pytestmark = pytest.mark.unit

A = TaskId("t-222222")
B = TaskId("t-333333")


def test_no_duplicates_returns_empty() -> None:
    entries = [(A, Location("a.md", 0)), (B, Location("b.md", 3))]

    assert find_duplicate_ids(entries) == ()


def test_duplicates_report_every_location_in_order() -> None:
    entries = [
        (A, Location("a.md", 0)),
        (B, Location("b.md", 1)),
        (A, Location("c.md", 7)),
        (A, Location("a.md", 4)),
    ]

    errors = find_duplicate_ids(entries)

    assert len(errors) == 1
    assert errors[0].task_id == A
    assert errors[0].locations == (Location("a.md", 0), Location("c.md", 7), Location("a.md", 4))
    assert "t-222222" in str(errors[0])
    assert isinstance(errors[0], DuplicateTaskIdError)
