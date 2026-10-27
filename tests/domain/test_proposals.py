"""Contrato `captura_v1`: el borrador de captura propuesto por un modelo se valida al crearse."""

import pytest

from lifeos.domain.errors import ValidationError
from lifeos.domain.proposals import MAX_DRAFT_CHARS, CaptureDraft

pytestmark = pytest.mark.unit


def test_capture_draft_accepts_text_up_to_the_limit() -> None:
    assert CaptureDraft("x" * MAX_DRAFT_CHARS).text == "x" * MAX_DRAFT_CHARS


@pytest.mark.parametrize("text", ["", "   ", "x" * (MAX_DRAFT_CHARS + 1)])
def test_capture_draft_rejects_empty_or_oversized_text(text: str) -> None:
    with pytest.raises(ValidationError):
        CaptureDraft(text)
