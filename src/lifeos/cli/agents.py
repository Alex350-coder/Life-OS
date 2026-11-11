"""Entrada `lifeos-agentes` (Fase 8, ADR-0004, ADR-0018): registro y especialistas.

Órdenes:
- `lista`: agentes del núcleo y agentes importados con su estado y sus problemas.
- `importar <archivo>`: valida y registra una versión en borrador. Nunca la activa.
- `probar <id>`: prueba controlada sin escrituras; un borrador pasa a probado.
- `aprobar | activar | desactivar <id>`; `restaurar <id> --version N`.
- `exportar <id> [--version N] [--salida archivo]`.
- `ejecutar <id> [--semana] [--ia]`: el especialista activo escribe sus propuestas.
- `propuestas [--semana]`, `aceptar <ref>`, `rechazar <ref>`: revisión del propietario.

Solo traduce argumentos y resultados. Códigos: 0 correcto, 1 rechazo o error, 2 uso o config.
"""

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn, TextIO

from lifeos.application.agent_registry import AgentRegistry, RegistryView
from lifeos.application.permissions import EffectivePermissions
from lifeos.application.proposal_review import ProposalReview, ReviewItem
from lifeos.application.specialists import SpecialistResult, SpecialistRunner
from lifeos.bootstrap import STARTUP_ERRORS, Runtime, Settings, build_runtime
from lifeos.domain.agent_lifecycle import LifecycleAction
from lifeos.domain.area_proposals import ProposalItem, ProposalKind, ProposalRef
from lifeos.domain.errors import LifeOSError
from lifeos.domain.text import printable
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.clock import Clock

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
MAX_IMPORT_BYTES = 64 * 1024
_LIFECYCLE = {
    "aprobar": LifecycleAction.APROBAR,
    "activar": LifecycleAction.ACTIVAR,
    "desactivar": LifecycleAction.DESACTIVAR,
    "restaurar": LifecycleAction.RESTAURAR,
}


class UsageError(Exception):
    """Argumentos no válidos; se informa sin la traza de argparse."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="lifeos-agentes", description="Registro de agentes (ADR-0018).")
    commands = parser.add_subparsers(dest="orden", required=True, parser_class=_Parser)
    commands.add_parser("lista", help="agentes y su estado")
    importar = commands.add_parser("importar", help="registrar un archivo en borrador")
    importar.add_argument("archivo")
    for name in ("probar", "aprobar", "activar", "desactivar"):
        commands.add_parser(name, help=f"{name} un agente importado").add_argument("id")
    restaurar = commands.add_parser("restaurar", help="volver a una versión anterior")
    restaurar.add_argument("id")
    restaurar.add_argument("--version", type=int, required=True)
    exportar = commands.add_parser("exportar", help="mostrar o guardar el archivo de una versión")
    exportar.add_argument("id")
    exportar.add_argument("--version", type=int)
    exportar.add_argument("--salida", help="archivo nuevo (no sobrescribe)")
    ejecutar = commands.add_parser("ejecutar", help="el especialista escribe sus propuestas")
    ejecutar.add_argument("id")
    ejecutar.add_argument("--semana", help="AAAA-Wnn (por defecto, la actual)")
    ejecutar.add_argument("--ia", action="store_true", help="usar el modelo (si no, heurística)")
    propuestas = commands.add_parser("propuestas", help="propuestas pendientes")
    propuestas.add_argument("--semana", help="AAAA-Wnn (por defecto, todas)")
    for name in ("aceptar", "rechazar"):
        commands.add_parser(name, help=f"{name} una propuesta").add_argument("ref")
    return parser


def _week(raw: str | None) -> IsoWeek | None:
    if raw is None:
        return None
    try:
        return IsoWeek.parse(raw)
    except LifeOSError:
        raise UsageError(f"semana no válida: {printable(raw)} (usa AAAA-Wnn)") from None


def _ref(raw: str) -> ProposalRef:
    try:
        return ProposalRef.parse(raw)
    except LifeOSError:
        raise UsageError(f"propuesta no válida: {printable(raw)} (AAAA-Wnn/<nota>/p<n>)") from None


# --- Salida -------------------------------------------------------------------------------------


def _permissions_lines(perms: EffectivePermissions) -> list[str]:
    reads = ", ".join(f"{root}/" for root in perms.reads) or "nada"
    writes = ", ".join(f"{g.root}/ ({g.mode})" for g in perms.writes) or "nada"
    return [
        f"  lectura: {reads}",
        f"  escritura: {writes}",
        f"  herramientas: {', '.join(sorted(perms.tools))}",
    ]


def _view_lines(view: RegistryView) -> list[str]:
    entry = view.entry
    area = f" · área {view.config.area}" if view.config and view.config.area else ""
    lines = [f"- {entry.agent_id} · v{entry.current} · {entry.state}{area}"]
    lines += [f"  ! {printable(problem)}" for problem in view.problems]
    return lines


def _print_list(runtime: Runtime, registry: AgentRegistry, out: TextIO) -> None:
    print("Núcleo (config/agents, siempre activos):", file=out)
    for agent in sorted(runtime.agents, key=lambda a: a.id):
        print(f"- {agent.id} · núcleo · activo", file=out)
    views = registry.views()
    print("Importados:", file=out)
    if not views:
        print("- ninguno. Importa uno con 'lifeos-agentes importar <archivo>'.", file=out)
    for view in views:
        for line in _view_lines(view):
            print(line, file=out)


def _describe(item: ProposalItem) -> str:
    if item.kind is ProposalKind.PRIORIDAD:
        return f"prioridad · {item.task_id} → {item.priority}"
    due = f" · vence {item.due.isoformat()}" if item.due else ""
    effort = item.effort.render() if item.effort else "?"
    return f"nueva tarea · {printable(item.title or '')} ({effort}){due} · {item.priority}"


def _print_result(result: SpecialistResult, out: TextIO) -> None:
    proposal = result.proposal
    mode = "Prueba (sin escrituras)" if result.dry_run else "Ejecución"
    print(
        f"{mode} de {proposal.agent_id} para {proposal.week} · fuente {proposal.source}", file=out
    )
    if not proposal.items:
        print("Nada que proponer.", file=out)
    for item in proposal.items:
        print(f"- {item.key} · {_describe(item)} · {printable(item.reason)}", file=out)
    for alert in result.alerts:
        print(f"! {printable(alert)}", file=out)
    if result.recorded_test:
        print("Versión marcada como probada. Siguiente: 'lifeos-agentes aprobar'.", file=out)
    if result.path is not None:
        print(
            f"Propuestas escritas en {result.path}. Revísalas con 'lifeos-agentes propuestas'.",
            file=out,
        )


def _pending_line(review: ReviewItem) -> str:
    item = review.item
    if item.kind is ProposalKind.PRIORIDAD:
        title = printable(review.task_title or str(item.task_id))
        current = review.current_priority or "?"
        what = f"prioridad · {title}: {current} → {item.priority}"
    else:
        what = _describe(item)
    return f"- {review.ref} · {what} · {printable(item.reason)}"


# --- Órdenes ------------------------------------------------------------------------------------


def _import(registry: AgentRegistry, raw_path: str, out: TextIO) -> int:
    path = Path(raw_path)
    try:
        if path.stat().st_size > MAX_IMPORT_BYTES:
            raise LifeOSError(f"{path.name} supera {MAX_IMPORT_BYTES} bytes")
        data = path.read_bytes()
    except OSError as error:
        raise LifeOSError(f"no se pudo leer {path.name}: {error.strerror}") from None
    entry = registry.import_config(data, path.name)
    print(f"{entry.agent_id}: v{entry.current} en {entry.state}.", file=out)
    view = next(v for v in registry.views() if v.entry.agent_id == entry.agent_id)
    if view.effective is not None:
        print("Permisos efectivos (solicitado ∩ política):", file=out)
        for line in _permissions_lines(view.effective):
            print(line, file=out)
    print("No se activa: pruébalo con 'lifeos-agentes probar', apruébalo y actívalo.", file=out)
    return EXIT_OK


def _export(registry: AgentRegistry, args: argparse.Namespace, out: TextIO) -> int:
    data = registry.export(args.id, version=args.version)
    if args.salida is None:
        out.write(data.decode("utf-8"))
        return EXIT_OK
    target = Path(args.salida)
    try:
        with target.open("xb") as handle:
            handle.write(data)
    except FileExistsError:
        raise LifeOSError(f"{target.name} ya existe; elige otro nombre") from None
    except OSError as error:
        raise LifeOSError(f"no se pudo escribir {target.name}: {error.strerror}") from None
    print(f"Exportado en {target}.", file=out)
    return EXIT_OK


def _runner(runtime: Runtime) -> SpecialistRunner:
    if runtime.specialists is None:
        raise LifeOSError("falta LIFEOS_VAULT_PATH: los especialistas leen la bóveda")
    return runtime.specialists


def _review(runtime: Runtime) -> ProposalReview:
    if runtime.proposals is None:
        raise LifeOSError("falta LIFEOS_VAULT_PATH: las propuestas viven en la bóveda")
    return runtime.proposals


def _dispatch(runtime: Runtime, args: argparse.Namespace, out: TextIO) -> int:
    registry = runtime.registry
    if registry is None:  # build_runtime siempre lo crea
        raise LifeOSError("registro de agentes no disponible")
    orden = args.orden
    if orden == "lista":
        _print_list(runtime, registry, out)
    elif orden == "importar":
        return _import(registry, args.archivo, out)
    elif orden == "probar":
        _print_result(_runner(runtime).run(args.id, dry_run=True), out)
    elif orden in _LIFECYCLE:
        entry = registry.transition(
            args.id, _LIFECYCLE[orden], version=getattr(args, "version", None)
        )
        print(f"{entry.agent_id}: v{entry.current} {entry.state}.", file=out)
    elif orden == "exportar":
        return _export(registry, args, out)
    elif orden == "ejecutar":
        _print_result(_runner(runtime).run(args.id, week=_week(args.semana)), out)
    elif orden == "propuestas":
        pending = _review(runtime).pending(_week(args.semana))
        if not pending:
            print("No hay propuestas pendientes.", file=out)
        for review in pending:
            print(_pending_line(review), file=out)
    elif orden == "aceptar":
        result = _review(runtime).accept(_ref(args.ref))
        task = f" (tarea {result.task_id})" if result.task_id else ""
        print(f"Aceptada {result.ref}{task}.", file=out)
    else:
        ref = _ref(args.ref)
        _review(runtime).reject(ref)
        print(f"Rechazada {ref}.", file=out)
    return EXIT_OK


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
        _week(getattr(args, "semana", None))
        if args.orden in ("aceptar", "rechazar"):
            _ref(args.ref)
    except UsageError as error:
        print(f"Uso incorrecto: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    try:
        settings = Settings.from_env(project_root, environ)
        runtime = build_runtime(settings, with_llm=bool(getattr(args, "ia", False)), clock=clock)
    except STARTUP_ERRORS as error:
        print(f"Error de configuración: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    try:
        return _dispatch(runtime, args, stdout)
    except LifeOSError as error:
        print(f"Error: {printable(str(error))}", file=stderr)
        return EXIT_REFUSED


if __name__ == "__main__":  # pragma: no cover - entrada manual
    sys.exit(main(sys.argv[1:]))
