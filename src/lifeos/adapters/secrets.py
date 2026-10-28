"""Secretos locales: lectura estricta de `.env` y un envoltorio que nunca se imprime (SEC-08).

`.env` no se interpreta como un shell: sin `export`, sin variables ni sustituciones. Los errores
indican el número de línea, nunca su contenido (podría ser una clave).
"""

import re
from collections.abc import Mapping
from pathlib import Path

from lifeos.domain.errors import LifeOSError

_KEY = r"[A-Z_][A-Z0-9_]*"
_LINE_RE = re.compile(rf"^({_KEY})=(?:\"([^\"\\$`]*)\"|'([^']*)'|([^\s\"'\\$`#]*))$")
MAX_DOTENV_BYTES = 16 * 1024


class MissingSecretError(LifeOSError):
    """Falta un secreto obligatorio en el entorno y en `.env`."""


class Secret:
    """Valor sensible. `str`, `repr` y f-strings muestran `***`; solo `reveal()` lo devuelve."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not value.strip() or "\n" in value or "\r" in value:
            raise ValueError("un secreto no puede estar vacío ni tener saltos de línea")
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "Secret('***')"

    def __str__(self) -> str:
        return "***"

    def __format__(self, spec: str) -> str:
        return "***"


def read_dotenv(path: Path) -> dict[str, str]:
    """Devuelve las asignaciones de `.env`; si el archivo no existe, un diccionario vacío."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {}
    if len(data) > MAX_DOTENV_BYTES:
        raise ValueError(f"{path.name}: supera {MAX_DOTENV_BYTES} bytes")
    try:
        text = data.decode("utf-8-sig")  # el Bloc de notas de Windows añade BOM
    except UnicodeDecodeError:
        raise ValueError(f"{path.name}: no está codificado en UTF-8") from None
    values: dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE_RE.match(stripped)
        if match is None:
            raise ValueError(f"{path.name}: línea {number} no tiene el formato CLAVE=valor")
        key, double, single, plain = match.groups()
        if key in values:
            raise ValueError(f"{path.name}: línea {number} repite una clave (duplicada)")
        values[key] = next(v for v in (double, single, plain) if v is not None)
    return values


def resolve_secret(name: str, *, environ: Mapping[str, str], dotenv: Mapping[str, str]) -> Secret:
    """El entorno del proceso tiene prioridad; `.env` nunca lo sobrescribe."""
    value = environ.get(name) or dotenv.get(name) or ""
    if not value.strip():
        raise MissingSecretError(f"falta la variable {name} (en el entorno o en .env)")
    return Secret(value)
