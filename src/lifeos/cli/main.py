"""CLI `lifeos` (F3-T05, F4-T05, ADR-0006).

Órdenes: `capturar`, `tareas`, `semana [--replanificar]`, `aprobar`, `hoy`, `checkin`, `cierre`
y `uso`.

Solo traduce argumentos y resultados; las reglas viven en los casos de uso. Códigos de salida:
0 correcto, 1 rechazo o error del caso de uso, 2 error de uso o de configuración.
"""

import argparse
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC
from pathlib import Path
from typing import NoReturn, TextIO

from lifeos.application.capture import capture
from lifeos.application.closing import DailyClosing
from lifeos.application.coach import CoachSession
from lifeos.application.jefe import JefePlanner, PlanOutcome
from lifeos.application.tasks import TaskQuery, query_tasks
from lifeos.application.tools import AuditIncompleteError
from lifeos.application.usage_report import summarize
from lifeos.bootstrap import STARTUP_ERRORS, Runtime, Settings, build_runtime
from lifeos.cli import output
from lifeos.domain.closing import AnswerKind, ClosingAnswer
from lifeos.domain.coach import CheckInState
from lifeos.domain.errors import LifeOSError, ValidationError
from lifeos.domain.ids import TaskId
from lifeos.domain.plan import PlanOrigin
from lifeos.domain.task import Area, Effort, Progress, TaskStatus
from lifeos.domain.text import printable
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.clock import Clock
from lifeos.ports.llm import LlmError
from lifeos.ports.vault import VaultStore

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
CLI_SOURCE = "cli"
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_MONTH_RE = re.compile(r"([0-9]{4})-(0[1-9]|1[0-2])")


class UsageError(Exception):
    """Argumentos no válidos; se informa sin la traza de argparse."""


class SetupError(Exception):
    """Falta algo en la configuración local para ejecutar la orden."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


CONFIG_ERRORS: tuple[type[Exception], ...] = (SetupError, *STARTUP_ERRORS)


def _week(text: str) -> IsoWeek:
    try:
        return IsoWeek.parse(text)
    except ValidationError as error:
        raise argparse.ArgumentTypeError(error.reason) from None


def _task_id(text: str) -> TaskId:
    try:
        return TaskId(text.strip())
    except ValidationError as error:
        raise argparse.ArgumentTypeError(error.reason) from None


def _pair(text: str) -> tuple[TaskId, str]:
    task, sep, value = text.partition("=")
    if not sep or not value.strip():
        raise argparse.ArgumentTypeError("formato esperado ID=valor (p. ej., t-7f3k2m=40)")
    return _task_id(task), value.strip()


def _progress(text: str) -> tuple[TaskId, Progress]:
    task, value = _pair(text)
    try:
        return task, Progress.parse(value if value.endswith("%") else f"{value}%")
    except ValidationError as error:
        raise argparse.ArgumentTypeError(error.reason) from None


def _remaining(text: str) -> tuple[TaskId, Effort]:
    task, value = _pair(text)
    try:
        return task, Effort.parse(value)
    except ValidationError as error:
        raise argparse.ArgumentTypeError(error.reason) from None


def _month(text: str) -> tuple[int, int]:
    match = _MONTH_RE.fullmatch(text.strip())
    if match is None:
        raise argparse.ArgumentTypeError("formato esperado AAAA-MM (mes 01 a 12)")
    return int(match[1]), int(match[2])


def _daily_commands(commands: "argparse._SubParsersAction[_Parser]") -> None:
    commands.add_parser("hoy", help="briefing del día: bloque actual, siguiente y check-ins")
    checkin = commands.add_parser("checkin", help="registrar cómo vas (el Coach responde)")
    checkin.add_argument("texto", nargs="*", help="nota libre (se guarda como dato)")
    checkin.add_argument("--estado", choices=[s.value for s in CheckInState], default="nota")
    checkin.add_argument("--tarea", type=_task_id, help="id de la tarea del bloque")
    checkin.add_argument("--capturar", help="guardar además una captura en 00-Inbox/")
    checkin.add_argument("--ia", action="store_true", help="pedir el mensaje al modelo")
    cierre = commands.add_parser("cierre", help="cierre del día (sin --confirmar no cambia nada)")
    cierre.add_argument("--hecho", type=_task_id, action="append", default=[], metavar="ID")
    cierre.add_argument("--avance", type=_progress, action="append", default=[], metavar="ID=40")
    cierre.add_argument("--restante", type=_remaining, action="append", default=[], metavar="ID=1h")
    cierre.add_argument("--no", type=_task_id, action="append", default=[], metavar="ID")
    cierre.add_argument("--confirmar", action="store_true", help="aplicar los cambios")
    uso = commands.add_parser("uso", help="resumen del uso de modelos de un mes (UTC)")
    uso.add_argument("--mes", type=_month, help="AAAA-MM; por defecto, el mes actual")


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="lifeos", description="Life OS: planificación personal.")
    commands = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    capturar = commands.add_parser("capturar", help="guardar una captura rápida en 00-Inbox/")
    capturar.add_argument("texto", nargs="+")
    tareas = commands.add_parser("tareas", help="listar tareas de la bóveda")
    tareas.add_argument("--area", choices=[a.value for a in Area])
    tareas.add_argument("--estado", choices=[s.value for s in TaskStatus])
    tareas.add_argument("--texto", help="buscar en el título")
    tareas.add_argument("--archivadas", action="store_true", help="incluir Archivo/")
    semana = commands.add_parser("semana", help="proponer el plan semanal (queda propuesto)")
    semana.add_argument("--semana", type=_week, help="AAAA-Wnn; por defecto, la semana actual")
    semana.add_argument("--ia", action="store_true", help="pedir la propuesta al modelo")
    semana.add_argument(
        "--replanificar",
        action="store_true",
        help="proponer una revisión de la semana aprobada desde ahora (ADR-0011)",
    )
    aprobar = commands.add_parser("aprobar", help="aprobar y publicar el plan propuesto")
    aprobar.add_argument("--semana", type=_week, help="AAAA-Wnn; por defecto, la semana actual")
    _daily_commands(commands)
    return parser


# --- Órdenes ----------------------------------------------------------------------------------


def _vault(runtime: Runtime) -> VaultStore:
    if runtime.vault is None:
        raise SetupError("falta LIFEOS_VAULT_PATH (ruta a tu bóveda) en el entorno o en .env")
    return runtime.vault


def _jefe(runtime: Runtime) -> JefePlanner:
    if runtime.jefe is None:
        raise SetupError("no hay agente Jefe configurado (config/agents/jefe.yaml y la política)")
    return runtime.jefe


def _capturar(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    text = " ".join(args.texto)
    path = capture(_vault(runtime), text, now=runtime.clock.now(), source=CLI_SOURCE)
    return EXIT_OK, [f"Captura guardada en {path}"]


def _tareas(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    query = TaskQuery(
        area=Area(args.area) if args.area else None,
        status=TaskStatus(args.estado) if args.estado else None,
        text=args.texto,
        include_archived=args.archivadas,
    )
    return EXIT_OK, output.tasks(query_tasks(_vault(runtime), query))


def _plan(result: PlanOutcome) -> tuple[int, list[str]]:
    return (EXIT_OK if result.accepted else EXIT_REFUSED), output.outcome(result)


def _semana(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    origin = PlanOrigin.IA if args.ia else PlanOrigin.HEURISTICA
    jefe = _jefe(runtime)
    if args.replanificar:
        return _plan(jefe.replanificar(args.semana, origin=origin))
    return _plan(jefe.proponer(args.semana, origin=origin))


def _aprobar(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    return _plan(_jefe(runtime).aprobar(args.semana))


def _coach(runtime: Runtime) -> CoachSession:
    if runtime.coach is None:
        raise SetupError("no hay agente Coach configurado (config/agents/coach.yaml y la política)")
    return runtime.coach


def _closing(runtime: Runtime) -> DailyClosing:
    if runtime.closing is None:
        raise SetupError("el cierre necesita la bóveda y el agente Coach configurados")
    return runtime.closing


def _hoy(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    return EXIT_OK, output.briefing(_coach(runtime).briefing())


def _checkin(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    result = _coach(runtime).checkin(
        " ".join(args.texto),
        state=CheckInState(args.estado),
        task_id=args.tarea,
        capture=args.capturar,
        use_ai=args.ia,
    )
    return EXIT_OK, output.checkin(result)


def _unique[V](pairs: Sequence[tuple[TaskId, V]], option: str) -> dict[TaskId, V]:
    """`ID=valor` sin repetir: un segundo valor para el mismo ID no pisa al primero."""
    values: dict[TaskId, V] = {}
    for task, value in pairs:
        if task in values:
            raise ValidationError("cierre", str(task), f"{option} repetido para la misma tarea")
        values[task] = value
    return values


def _answers(args: argparse.Namespace) -> list[ClosingAnswer]:
    answers = [ClosingAnswer(task, AnswerKind.HECHA) for task in args.hecho]
    progress = _unique(args.avance, "--avance")
    remaining = _unique(args.restante, "--restante")
    for task in dict.fromkeys([*progress, *remaining]):
        answers.append(
            ClosingAnswer(task, AnswerKind.PARCIAL, progress.get(task), remaining.get(task))
        )
    answers += [ClosingAnswer(task, AnswerKind.NO) for task in args.no]
    return answers


def _cierre(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    closing = _closing(runtime)
    answers = _answers(args)
    if args.confirmar:
        return EXIT_OK, output.closing_result(closing.apply(answers))
    return EXIT_OK, output.closing_preview(closing.preview(answers))


def _uso(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    now = runtime.clock.now().astimezone(UTC)
    year, month = args.mes or (now.year, now.month)
    summary = summarize(runtime.usage_reader.read_month(year, month))
    return EXIT_OK, output.usage(summary, f"{year:04d}-{month:02d}")


Command = Callable[[Runtime, argparse.Namespace], tuple[int, list[str]]]
COMMANDS: Mapping[str, Command] = {
    "capturar": _capturar,
    "tareas": _tareas,
    "semana": _semana,
    "aprobar": _aprobar,
    "hoy": _hoy,
    "checkin": _checkin,
    "cierre": _cierre,
    "uso": _uso,
}
# Órdenes que no tocan la bóveda.
WITHOUT_VAULT = frozenset({"uso"})


# --- Entrada ----------------------------------------------------------------------------------


def _runtime(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None,
    project_root: Path,
    clock: Clock | None,
) -> Runtime:
    settings = Settings.from_env(project_root, environ)
    if settings.vault_path is None and args.command not in WITHOUT_VAULT:
        raise SetupError("falta LIFEOS_VAULT_PATH (ruta a tu bóveda) en el entorno o en .env")
    with_llm = args.command in {"semana", "checkin"} and args.ia
    return build_runtime(settings, with_llm=with_llm, clock=clock)


def _utf8(stream: TextIO) -> TextIO:
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:  # consola de Windows: evita errores con "–" o acentos
        reconfigure(encoding="utf-8", errors="replace")
    return stream


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path = DEFAULT_PROJECT_ROOT,
    clock: Clock | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    stdout = out or _utf8(sys.stdout)
    stderr = err or _utf8(sys.stderr)
    try:
        args = build_parser().parse_args(argv)
        runtime = _runtime(args, environ, project_root, clock)
        code, lines = COMMANDS[args.command](runtime, args)
    except UsageError as error:
        print(f"Error de uso: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    except CONFIG_ERRORS as error:
        print(f"Error de configuración: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    except AuditIncompleteError as error:
        print(
            f"Aviso: la escritura SÍ se hizo, pero no quedó auditada: {printable(str(error))}",
            file=stderr,
        )
        return EXIT_REFUSED
    except LlmError as error:
        print(
            f"Error del modelo: {printable(str(error))}. Puedes repetir la orden sin --ia.",
            file=stderr,
        )
        return EXIT_REFUSED
    except LifeOSError as error:
        print(f"Error: {printable(str(error))}", file=stderr)
        return EXIT_REFUSED
    print("\n".join(printable(line) for line in lines), file=stdout)
    return code


if __name__ == "__main__":  # pragma: no cover - entrada manual
    sys.exit(main())
