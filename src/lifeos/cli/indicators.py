"""Entrada `lifeos-indicadores` (F9-T03, PLAN §13): utilidad real y carga de avisos.

`lifeos-indicadores [--dias N]` muestra los indicadores de los últimos N días (28 por defecto).
Solo lectura y sin IA. Solo traduce argumentos y resultados.
Códigos: 0 correcto, 1 error del caso de uso, 2 uso o configuración.
"""

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, TextIO

from lifeos.application.indicator_report import IndicatorReport, measure_indicators
from lifeos.application.indicators import DEFAULT_WINDOW_DAYS, MAX_WINDOW_DAYS
from lifeos.bootstrap import STARTUP_ERRORS, Settings, build_runtime
from lifeos.domain.errors import LifeOSError
from lifeos.domain.text import printable
from lifeos.ports.clock import Clock

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
UNKNOWN = "desconocido"


class UsageError(Exception):
    """Argumentos no válidos; se informa sin la traza de argparse."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _days(text: str) -> int:
    if not text.isdigit() or not 1 <= int(text) <= MAX_WINDOW_DAYS:
        raise argparse.ArgumentTypeError(f"debe ser un número de 1 a {MAX_WINDOW_DAYS}")
    return int(text)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="lifeos-indicadores",
        description="Indicadores de utilidad (PLAN §13) y carga de notificaciones. Solo lectura.",
    )
    parser.add_argument("--dias", type=_days, default=DEFAULT_WINDOW_DAYS)
    return parser


def _pct(value: int | None) -> str:
    return UNKNOWN if value is None else f"{value} %"


def render(report: IndicatorReport) -> list[str]:
    d, a, u, p, n = (
        report.deliveries,
        report.adherence,
        report.usage,
        report.acceptance,
        report.notifications,
    )
    lines = [
        f"Indicadores del {report.window.start} al {report.window.end}",
        "",
        f"Entregas cumplidas: {d.completed} de {d.due} ({_pct(d.percent)})",
        f"Tareas olvidadas (abiertas con la fecha límite pasada): {d.forgotten}",
        f"Cumplimiento del plan diario: {_pct(a.percent)} en {a.closed_days} cierres",
        f"Uso: {u.active_days} días activos ({u.days_per_week} por semana)",
        f"Planes aprobados con cambios menores: {p.minor_changes} de {p.approved} "
        f"({_pct(p.percent)}); semanas con plan: {p.proposed}",
        "",
        "Costo y latencia por ritual:",
    ]
    lines += [
        f"- {c.agent}/{c.ritual}: {c.calls} llamadas, {c.errors} con error, mediana "
        f"{c.latency_median_ms} ms, p95 {c.latency_p95_ms} ms, {c.cost_estimated} USD estimados"
        + (f" ({c.cost_unknown} intentos sin costo conocido)" if c.cost_unknown else "")
        for c in report.costs
    ] or ["- sin llamadas a modelos en la ventana"]
    per_day = UNKNOWN if n.per_active_day is None else str(n.per_active_day)
    kinds = ", ".join(f"{kind} {count}" for kind, count in n.by_kind) or "ninguno"
    lines += [
        "",
        f"Notificaciones: {n.sent} enviadas, {n.failed} fallidas, {n.skipped} omitidas",
        f"- días con avisos: {n.days_with_notifications}; máximo en un día: {n.max_per_day}; "
        f"media por día con avisos: {per_day}",
        f"- por tipo: {kinds}",
        "",
        "Sin medición automática (los valora el propietario):",
        *(f"- {name}: {reason}" for name, reason in report.not_measured),
    ]
    q = report.quality
    if q.malformed_log_lines or q.unreadable_notes:
        lines += [
            "",
            f"Aviso: {q.malformed_log_lines} líneas de log y {q.unreadable_notes} notas o tareas "
            "no se pudieron leer y no cuentan.",
        ]
    return [printable(line) for line in lines]


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path = DEFAULT_PROJECT_ROOT,
    out: TextIO | None = None,
    err: TextIO | None = None,
    clock: Clock | None = None,
) -> int:
    stdout, stderr = out or sys.stdout, err or sys.stderr
    try:
        args = _parser().parse_args(argv)
    except UsageError as error:
        print(f"Uso incorrecto: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    try:
        settings = Settings.from_env(project_root, environ)
        runtime = build_runtime(settings, clock=clock)
    except STARTUP_ERRORS as error:
        print(f"Error de configuración: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    if runtime.vault is None:
        print("Error de configuración: falta LIFEOS_VAULT_PATH", file=stderr)
        return EXIT_USAGE
    try:
        report = measure_indicators(
            runtime.vault,
            runtime.usage_reader,
            runtime.audit_reader,
            runtime.clock.now(),
            days=args.dias,
        )
    except LifeOSError as error:
        print(f"Error: {printable(str(error))}", file=stderr)
        return EXIT_REFUSED
    print("\n".join(render(report)), file=stdout)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entrada manual
    sys.exit(main(sys.argv[1:]))
