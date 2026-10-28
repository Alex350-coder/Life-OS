"""CLI `lifeos` (F3-T05, ADR-0006): `capturar`, `tareas`, `semana` y `aprobar`.

Solo traduce argumentos y resultados; las reglas viven en los casos de uso. Códigos de salida:
0 correcto, 1 rechazo o error del caso de uso, 2 error de uso o de configuración.
"""

import argparse
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import NoReturn, TextIO

from lifeos.application.capture import capture
from lifeos.application.jefe import JefePlanner, PlanOutcome
from lifeos.application.tasks import TaskQuery, query_tasks
from lifeos.application.tools import AuditIncompleteError
from lifeos.bootstrap import STARTUP_ERRORS, Runtime, Settings, build_runtime
from lifeos.cli import output
from lifeos.domain.errors import LifeOSError, ValidationError
from lifeos.domain.plan import PlanOrigin
from lifeos.domain.task import Area, TaskStatus
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.clock import Clock
from lifeos.ports.llm import LlmError
from lifeos.ports.vault import VaultStore

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
CLI_SOURCE = "cli"
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]


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
    aprobar = commands.add_parser("aprobar", help="aprobar y publicar el plan propuesto")
    aprobar.add_argument("--semana", type=_week, help="AAAA-Wnn; por defecto, la semana actual")
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
    return _plan(_jefe(runtime).proponer(args.semana, origin=origin))


def _aprobar(runtime: Runtime, args: argparse.Namespace) -> tuple[int, list[str]]:
    return _plan(_jefe(runtime).aprobar(args.semana))


Command = Callable[[Runtime, argparse.Namespace], tuple[int, list[str]]]
COMMANDS: Mapping[str, Command] = {
    "capturar": _capturar,
    "tareas": _tareas,
    "semana": _semana,
    "aprobar": _aprobar,
}


# --- Entrada ----------------------------------------------------------------------------------


def _runtime(
    args: argparse.Namespace,
    environ: Mapping[str, str] | None,
    project_root: Path,
    clock: Clock | None,
) -> Runtime:
    settings = Settings.from_env(project_root, environ)
    if settings.vault_path is None:
        raise SetupError("falta LIFEOS_VAULT_PATH (ruta a tu bóveda) en el entorno o en .env")
    with_llm = args.command == "semana" and args.ia
    return build_runtime(settings, with_llm=with_llm, clock=clock)


def _printable(line: str) -> str:
    """Sin caracteres de control: un título de la bóveda no puede manipular la terminal."""
    return "".join(ch for ch in line if unicodedata.category(ch) != "Cc")


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
        print(f"Error de uso: {error}", file=stderr)
        return EXIT_USAGE
    except CONFIG_ERRORS as error:
        print(f"Error de configuración: {_printable(str(error))}", file=stderr)
        return EXIT_USAGE
    except AuditIncompleteError as error:
        print(f"Aviso: la escritura SÍ se hizo, pero no quedó auditada: {error}", file=stderr)
        return EXIT_REFUSED
    except LlmError as error:
        print(f"Error del modelo: {error}. Puedes proponer sin --ia (heurística).", file=stderr)
        return EXIT_REFUSED
    except LifeOSError as error:
        print(f"Error: {_printable(str(error))}", file=stderr)
        return EXIT_REFUSED
    print("\n".join(_printable(line) for line in lines), file=stdout)
    return code


if __name__ == "__main__":  # pragma: no cover - entrada manual
    sys.exit(main())
