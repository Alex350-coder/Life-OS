"""Semana ISO 8601 (`AAAA-Wnn`), unidad del plan semanal."""

import re
from dataclasses import dataclass
from datetime import date, timedelta

from lifeos.domain.errors import ValidationError

DAYS_PER_WEEK = 7
_WEEK_RE = re.compile(r"(\d{4})-W(\d{2})")


@dataclass(frozen=True, slots=True, order=True)
class IsoWeek:
    year: int
    week: int

    def __post_init__(self) -> None:
        try:
            date.fromisocalendar(self.year, self.week, 1)
        except ValueError:
            raise ValidationError("semana", self.render(), "no existe en ese año ISO") from None

    @classmethod
    def parse(cls, text: str) -> "IsoWeek":
        match = _WEEK_RE.fullmatch(text.strip())
        if match is None:
            raise ValidationError("semana", text, "formato esperado: AAAA-Wnn")
        return cls(int(match[1]), int(match[2]))

    @classmethod
    def containing(cls, day: date) -> "IsoWeek":
        iso = day.isocalendar()
        return cls(iso.year, iso.week)

    def days(self) -> tuple[date, ...]:
        return tuple(
            date.fromisocalendar(self.year, self.week, weekday)
            for weekday in range(1, DAYS_PER_WEEK + 1)
        )

    def next(self) -> "IsoWeek":
        return IsoWeek.containing(self.days()[-1] + timedelta(days=1))

    def render(self) -> str:
        return f"{self.year:04d}-W{self.week:02d}"

    def __str__(self) -> str:
        return self.render()
