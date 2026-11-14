"""Entrada `lifeos-respaldo` (SEC-14, ADR-0016): crear, verificar y restaurar respaldos cifrados.

La frase se pide por teclado sin mostrarla (dos veces al crear) o se toma de
`LIFEOS_BACKUP_PASSPHRASE` para un respaldo programado. Nunca se imprime ni se registra.
Restaurar escribe en una carpeta nueva: la bóveda real no se toca nunca.
"""

import argparse
import getpass
import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from lifeos.bootstrap import (
    BACKUP_PASSPHRASE_ENV,
    STARTUP_ERRORS,
    Settings,
    backup_dir,
    vault_backup,
)
from lifeos.domain.text import printable
from lifeos.ports.backup import BackupError, BackupInfo, VaultBackup

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
KIB = 1024
DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lifeos-respaldo", description="Respaldo cifrado de la bóveda (SEC-14)."
    )
    commands = parser.add_subparsers(dest="orden", required=True)
    crear = commands.add_parser("crear", help="cifrar toda la bóveda en un archivo nuevo")
    crear.add_argument("--destino", type=Path, help="carpeta de respaldos (LIFEOS_BACKUP_DIR)")
    verificar = commands.add_parser("verificar", help="descifrar y comprobar, sin escribir nada")
    verificar.add_argument("archivo", type=Path)
    restaurar = commands.add_parser("restaurar", help="restaurar en una carpeta nueva o vacía")
    restaurar.add_argument("archivo", type=Path)
    restaurar.add_argument("--destino", type=Path, required=True)
    return parser


def _summary(verb: str, info: BackupInfo) -> str:
    return f"{verb}: {info.path} ({info.files} archivos, {info.size_bytes // KIB} KiB sin cifrar)"


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    project_root: Path = DEFAULT_PROJECT_ROOT,
    out: TextIO | None = None,
    err: TextIO | None = None,
    ask: Callable[[str], str] = getpass.getpass,
    backup: VaultBackup | None = None,
    now: datetime | None = None,
) -> int:
    stdout, stderr = out or sys.stdout, err or sys.stderr
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env(project_root, environ)
    except STARTUP_ERRORS as error:
        print(f"Error de configuración: {printable(str(error))}", file=stderr)
        return EXIT_USAGE
    engine = backup or vault_backup()
    preset = settings.environ.get(BACKUP_PASSPHRASE_ENV)
    try:
        if args.orden == "crear":
            if settings.vault_path is None:
                print("Error de configuración: falta LIFEOS_VAULT_PATH", file=stderr)
                return EXIT_USAGE
            phrase = preset or ask("Frase del respaldo: ")
            if not preset and ask("Repite la frase: ") != phrase:
                print("Las frases no coinciden; no se creó nada.", file=stderr)
                return EXIT_REFUSED
            target = args.destino or backup_dir(settings)
            info = engine.create(settings.vault_path, target, phrase, now or datetime.now(UTC))
            print(_summary("Respaldo creado", info), file=stdout)
            print("Guarda la frase: sin ella el respaldo no se puede abrir.", file=stdout)
        elif args.orden == "verificar":
            info = engine.verify(args.archivo, preset or ask("Frase del respaldo: "))
            print(_summary("Respaldo correcto", info), file=stdout)
        else:
            info = engine.restore(args.archivo, preset or ask("Frase del respaldo: "), args.destino)
            print(_summary("Restaurado en", info), file=stdout)
            print("Revisa la copia y, si es correcta, ábrela en Obsidian como bóveda.", file=stdout)
    except BackupError as error:
        print(f"Error: {printable(str(error))}", file=stderr)
        return EXIT_REFUSED
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - entrada manual
    sys.exit(main(sys.argv[1:]))
