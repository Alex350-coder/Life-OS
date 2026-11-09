"""Carga adaptativa (DT-01): baja el foco si se cumple poco; nunca supera el límite fijado."""

import pytest

from lifeos.domain.adaptive_load import DayScore, advise, closing_score

pytestmark = pytest.mark.unit
SIX_HOURS = 360


def _note(*entries: str) -> str:
    lines = "\n".join(f"- 22:30 · {entry}" for entry in entries)
    return f"# Día\n\n## Bloques\n- 09:00–10:00 · algo\n\n## Cierre\n{lines}\n"


def test_closing_score_counts_answers_and_ignores_the_unanswered() -> None:
    score = closing_score(
        _note(
            "hecha: t-aaaaaa",
            "avance: t-bbbbbb 20 % → 60 %",
            "no realizada: t-cccccc (propuesta de reubicación al Jefe)",
            "sin respuesta: t-dddddd (sin cambios)",
        )
    )
    assert score == DayScore(1.5, 3)


def test_a_day_without_closing_has_no_score() -> None:
    assert closing_score("# Día\n\n## Cierre\n") is None
    assert closing_score("# Día\n") is None


def test_too_few_days_keep_the_owner_limit() -> None:
    advice = advise(SIX_HOURS, [DayScore(0, 3), DayScore(0, 3)])
    assert advice.focus_max == SIX_HOURS
    assert advice.ratio is None
    assert "hacen falta 3" in advice.reason


@pytest.mark.parametrize(
    ("points", "expected"),
    [(3.0, SIX_HOURS), (2.4, SIX_HOURS), (2.0, 300), (1.0, 240)],
)
def test_focus_follows_the_completion_ratio(points: float, expected: int) -> None:
    advice = advise(SIX_HOURS, [DayScore(points, 3)] * 3)
    assert advice.focus_max == expected
    assert advice.focus_max <= SIX_HOURS


def test_reduced_load_explains_itself() -> None:
    advice = advise(SIX_HOURS, [DayScore(1, 3)] * 4)
    assert advice.reason.startswith("carga reducida a 4h/día")
    assert "33 %" in advice.reason or "33%" in advice.reason


def test_never_below_one_hour() -> None:
    assert advise(90, [DayScore(0, 2)] * 3).focus_max == 60
