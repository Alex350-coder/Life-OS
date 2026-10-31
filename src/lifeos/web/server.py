"""Entrada `lifeos-web` (ADR-0012, ADR-0013): servidor local solo en 127.0.0.1.

Valida la configuración, genera el token de arranque y muestra la URL de un solo uso. Códigos
de salida como la CLI: 0 correcto, 2 error de uso o de configuración.
"""

import sys
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

import uvicorn

from lifeos.bootstrap import STARTUP_ERRORS, Settings
from lifeos.domain.text import printable
from lifeos.web.app import create_app
from lifeos.web.security import LOOPBACK, LaunchToken, check_bind_host

EXIT_OK = 0
EXIT_USAGE = 2
DEFAULT_PORT = 8765
MIN_PORT, MAX_PORT = 1024, 65535
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]
DIST_DIR = "web/dist"
STARTUP_OR_VALUE: tuple[type[Exception], ...] = (ValueError, *STARTUP_ERRORS)


def _port(environ: Mapping[str, str]) -> int:
    raw = environ.get("LIFEOS_WEB_PORT", "").strip()
    if not raw:
        return DEFAULT_PORT
    if not raw.isdigit() or not MIN_PORT <= int(raw) <= MAX_PORT:
        raise ValueError(f"LIFEOS_WEB_PORT debe ser un número entre {MIN_PORT} y {MAX_PORT}")
    return int(raw)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path = DEFAULT_PROJECT_ROOT,
    out: TextIO | None = None,
    err: TextIO | None = None,
    serve: bool = True,
) -> int:
    stdout, stderr = out or sys.stdout, err or sys.stderr
    if argv:  # sin argumentos: todo se configura con el entorno o con .env
        print("Error de uso: lifeos-web no acepta argumentos", file=stderr)
        return EXIT_USAGE
    try:
        settings = Settings.from_env(project_root, environ)
        port = _port(settings.environ)
        check_bind_host(LOOPBACK)
        token = LaunchToken(time.monotonic)
        app = create_app(settings, port=port, launch_token=token, dist_dir=project_root / DIST_DIR)
    except STARTUP_OR_VALUE as error:
        print(f"Error de configuración: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    print(
        f"Life OS web en http://{LOOPBACK}:{port}/\n"
        f"Abre esta URL (un solo uso, caduca en 5 minutos):\n"
        f"  http://{LOOPBACK}:{port}/auth?token={token.value}\n"
        "No la compartas. Ctrl+C para detener.",
        file=stdout,
        flush=True,
    )
    if serve:  # pragma: no cover - arranca el servidor real
        uvicorn.run(app, host=LOOPBACK, port=port, access_log=False, server_header=False)
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entrada manual
    sys.exit(main(sys.argv[1:]))
