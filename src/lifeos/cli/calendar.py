"""Entrada `lifeos-calendario` (Fase 7, ADR-0017, SEC-13).

Órdenes:
- `estado`: qué permisos están conectados.
- `conectar --lectura | --escritura`: autoriza un permiso en el navegador (uno cada vez).
- `disponibilidad [--semana]`: intervalos ocupados y choques con el plan vigente.
- `publicar [--semana] [--confirmar]`: sin `--confirmar` solo muestra el cambio; con él lo aplica.
- `revocar`: revoca el acceso en Google y borra los tokens locales.

Solo traduce argumentos y resultados. Códigos de salida: 0 correcto, 1 rechazo o error,
2 error de uso o de configuración.
"""

import argparse
import sys
import webbrowser
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import NoReturn, TextIO
from zoneinfo import ZoneInfo

from lifeos.application.calendar_publication import CalendarPreview, CalendarPublisher
from lifeos.application.jefe import ExternalView
from lifeos.bootstrap import STARTUP_ERRORS, Settings, build_runtime, calendar_access
from lifeos.cli.output import console
from lifeos.domain.errors import LifeOSError
from lifeos.domain.text import printable
from lifeos.domain.weeks import IsoWeek
from lifeos.ports.calendar import CalendarAccess, GrantStatus
from lifeos.ports.clock import Clock

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
PERMISSIONS_URL = "https://myaccount.google.com/permissions"


class UsageError(Exception):
    """Argumentos no válidos; se informa sin la traza de argparse."""


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="lifeos-calendario", description="Google Calendar (ADR-0017).")
    commands = parser.add_subparsers(dest="orden", required=True, parser_class=_Parser)
    commands.add_parser("estado", help="permisos conectados")
    conectar = commands.add_parser("conectar", help="autorizar un permiso en el navegador")
    which = conectar.add_mutually_exclusive_group(required=True)
    which.add_argument("--lectura", action="store_true", help="disponibilidad (freebusy)")
    which.add_argument("--escritura", action="store_true", help="calendario propio Life OS")
    disponibilidad = commands.add_parser("disponibilidad", help="ocupado y choques con el plan")
    disponibilidad.add_argument("--semana", help="AAAA-Wnn (por defecto, la actual)")
    publicar = commands.add_parser("publicar", help="publicar el plan aprobado")
    publicar.add_argument("--semana", help="AAAA-Wnn (por defecto, la actual)")
    publicar.add_argument("--confirmar", action="store_true", help="aplicar el cambio mostrado")
    commands.add_parser("revocar", help="revocar el acceso y borrar los tokens locales")
    return parser


def _week(raw: str | None) -> IsoWeek | None:
    if raw is None:
        return None
    try:
        return IsoWeek.parse(raw)
    except LifeOSError:
        raise UsageError(f"semana no válida: {printable(raw)} (usa AAAA-Wnn)") from None


def _status_line(status: GrantStatus) -> str:
    state = (
        f"conectado desde {status.obtained:%Y-%m-%d %H:%M} UTC"
        if status.connected and status.obtained is not None
        else "sin conectar"
    )
    problem = f" — {printable(status.problem)}" if status.problem else ""
    return f"- {status.grant}: {state} ({status.scope}){problem}"


def _span(zone: ZoneInfo, start: datetime, end: datetime) -> str:
    a, b = start.astimezone(zone), end.astimezone(zone)
    tail = f"{b:%H:%M}" if b.date() == a.date() else f"{b:%Y-%m-%d %H:%M}"
    return f"{a:%Y-%m-%d %H:%M}–{tail}"


def _print_availability(view: ExternalView, out: TextIO) -> None:
    print(f"Disponibilidad externa de {view.week} (solo intervalos ocupados):", file=out)
    if not view.external:
        print("- Sin intervalos ocupados.", file=out)
    for item in view.external:
        span = _span(view.zone, item.interval.start, item.interval.end)
        print(f"- {span} · {item.source} · confianza {item.confidence.value}", file=out)
    if view.path is None:
        print("Sin plan para esta semana.", file=out)
        return
    state = view.status.value if view.status is not None else "?"
    print(f"Plan vigente: {view.path} ({state})", file=out)
    if not view.clashes:
        print("Ningún bloque de trabajo choca con el calendario.", file=out)
    for block, hit in view.clashes:
        span = _span(view.zone, block.interval.start, block.interval.end)
        print(
            f"! Choque: '{printable(block.title)}' {span} con {hit.source} "
            f"(confianza {hit.confidence.value}). Usa 'lifeos semana --replanificar'.",
            file=out,
        )


def _print_preview(preview: CalendarPreview, out: TextIO) -> None:
    sync = preview.sync
    print(f"Plan aprobado {preview.path} → calendario 'Life OS':", file=out)
    for entry in sync.create:
        span = _span(preview.zone, entry.interval.start, entry.interval.end)
        print(f"+ crear   {span} · {printable(entry.title)}", file=out)
    for _event_id, entry in sync.update:
        span = _span(preview.zone, entry.interval.start, entry.interval.end)
        print(f"~ ajustar {span} · {printable(entry.title)}", file=out)
    for event in sync.delete:
        span = _span(preview.zone, event.interval.start, event.interval.end)
        print(f"- borrar  {span} · {printable(event.title)}", file=out)
    print(f"Sin cambios: {sync.unchanged} evento(s).", file=out)
    for warning in preview.warnings:
        print(f"! Aviso: {printable(warning)}", file=out)


def _publish(publisher: CalendarPublisher, week: IsoWeek | None, confirm: bool, out: TextIO) -> int:
    if not confirm:
        preview = publisher.preview(week)
        _print_preview(preview, out)
        if not preview.sync.is_empty:
            print("Nada se publicó. Revisa el cambio y repite con --confirmar.", file=out)
        return EXIT_OK
    result = publisher.publish(week)
    _print_preview(result.preview, out)
    applied = result.applied
    print(
        f"Publicado: {applied.created} creado(s), {applied.updated} ajustado(s), "
        f"{applied.deleted} borrado(s).",
        file=out,
    )
    return EXIT_OK


def _revoke(access: CalendarAccess, out: TextIO) -> int:
    report = access.revoke()
    if not (report.revoked or report.already_invalid or report.removed):
        print("No había tokens de Google Calendar que revocar.", file=out)
        return EXIT_OK
    if report.revoked:
        print(f"Revocado en Google: {', '.join(report.revoked)}.", file=out)
    if report.already_invalid:
        print(f"Google ya no los reconocía: {', '.join(report.already_invalid)}.", file=out)
    print(f"Tokens locales borrados: {', '.join(report.removed) or 'ninguno'}.", file=out)
    print(f"Comprueba que Life OS ya no aparece en {PERMISSIONS_URL}", file=out)
    return EXIT_OK


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path = DEFAULT_PROJECT_ROOT,
    out: TextIO | None = None,
    err: TextIO | None = None,
    open_browser: Callable[[str], object] | None = None,
    clock: Clock | None = None,
) -> int:
    stdout, stderr = out or console(sys.stdout), err or console(sys.stderr, error=True)
    try:
        args = _parser().parse_args(argv)
        week = _week(getattr(args, "semana", None))
    except UsageError as error:
        print(f"Uso incorrecto: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    try:
        settings = Settings.from_env(project_root, environ)
        if args.orden in ("estado", "conectar", "revocar"):
            access = calendar_access(settings, clock=clock)
        else:
            runtime = build_runtime(settings, clock=clock)
    except STARTUP_ERRORS as error:
        print(f"Error de configuración: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    try:
        if args.orden == "estado":
            for status in access.status():
                print(_status_line(status), file=stdout)
            return EXIT_OK
        if args.orden == "conectar":
            grant = "lectura" if args.lectura else "escritura"
            browser = open_browser or _browser(stdout)
            print(f"Abriendo el navegador para autorizar {grant}…", file=stdout)
            print(_status_line(access.connect(grant, browser)), file=stdout)
            return EXIT_OK
        if args.orden == "revocar":
            return _revoke(access, stdout)
        if runtime.jefe is None or runtime.calendar_publisher is None:
            print("Error de configuración: falta LIFEOS_VAULT_PATH o el Jefe", file=stderr)
            return EXIT_USAGE
        if args.orden == "disponibilidad":
            _print_availability(runtime.jefe.eventos_externos(week), stdout)
            return EXIT_OK
        return _publish(runtime.calendar_publisher, week, args.confirmar, stdout)
    except LifeOSError as error:
        print(f"Error: {printable(str(error))}", file=stderr)
        return EXIT_REFUSED


def _browser(out: TextIO) -> Callable[[str], object]:
    def open_url(url: str) -> object:
        # Si no se abre solo, el propietario puede copiar la dirección (no contiene secretos).
        print(f"Si no se abre, visita: {url}", file=out)
        return webbrowser.open(url)

    return open_url


if __name__ == "__main__":  # pragma: no cover - entrada manual
    sys.exit(main(sys.argv[1:]))
