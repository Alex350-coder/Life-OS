"""Resumen del registro de uso de modelos (F4-T05, PLAN §11, ADR-0008).

Agrupa por agente y ritual: llamadas (por identificador de correlación), intentos, errores,
tokens y costo. Los tokens y costos desconocidos se cuentan aparte y nunca se suman como 0.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from decimal import Decimal

from lifeos.domain.plan import clean
from lifeos.ports.logs import CostStatus, UsageOutcome, UsageRecord, UsageScan

MAX_NAME_CHARS = 40
TOTAL = "total"


@dataclass(frozen=True, slots=True)
class UsageRow:
    agent: str
    ritual: str
    calls: int
    attempts: int
    errors: int
    tokens_prompt: int
    tokens_completion: int
    tokens_unknown: int  # intentos sin conteo de tokens del proveedor
    cost_estimated: Decimal
    cost_unknown: int  # intentos sin costo conocido (falta tarifa o tokens)


@dataclass(frozen=True, slots=True)
class UsageSummary:
    rows: tuple[UsageRow, ...]
    total: UsageRow
    malformed: int
    files: int


def _name(text: str) -> str:
    printable = "".join(ch if ch.isprintable() else " " for ch in text)
    return clean(printable, MAX_NAME_CHARS)


def _row(agent: str, ritual: str, records: Iterable[UsageRecord]) -> UsageRow:
    items = list(records)
    return UsageRow(
        agent=agent,
        ritual=ritual,
        calls=len({r.correlation_id for r in items}),
        attempts=len(items),
        errors=sum(1 for r in items if r.outcome is not UsageOutcome.OK),
        tokens_prompt=sum(r.tokens_prompt or 0 for r in items),
        tokens_completion=sum(r.tokens_completion or 0 for r in items),
        tokens_unknown=sum(
            1 for r in items if r.tokens_prompt is None or r.tokens_completion is None
        ),
        cost_estimated=sum((r.cost_usd or Decimal(0) for r in items), Decimal(0)),
        cost_unknown=sum(1 for r in items if r.cost_status is CostStatus.DESCONOCIDO),
    )


def summarize(scan: UsageScan) -> UsageSummary:
    groups: dict[tuple[str, str], list[UsageRecord]] = defaultdict(list)
    for record in scan.records:
        groups[(_name(record.agent), _name(record.ritual))].append(record)
    rows = tuple(_row(agent, ritual, groups[(agent, ritual)]) for agent, ritual in sorted(groups))
    every = _row(TOTAL, TOTAL, scan.records)
    total = replace(every, calls=sum(row.calls for row in rows))  # llamadas por agente y ritual
    return UsageSummary(
        rows=rows,
        total=total,
        malformed=scan.malformed,
        files=scan.files,
    )
