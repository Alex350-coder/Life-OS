"""Puerto de almacenamiento de la bóveda (ADR-0001) y validación léxica de rutas (SEC-02)."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from lifeos.domain.errors import LifeOSError

_RESERVED_NAMES = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_FORBIDDEN_CHARS = re.compile(r'[\\:*?"<>|\x00-\x1f]')
# Caracteres invisibles o separadores que harían que dos rutas parezcan iguales sin serlo.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Zs"})
MAX_COMPONENT_LENGTH = 255
MAX_PATH_LENGTH = 1024
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# `ARCHIV~1` es un alias de `Archivo` en NTFS: compararía distinto y apuntaría al mismo sitio.
_SHORT_NAME_RE = re.compile(r"~\d")


class VaultError(LifeOSError):
    """Error del acceso a la bóveda."""


class PathRejectedError(VaultError):
    """La ruta no es una ruta relativa segura dentro de la bóveda."""

    def __init__(self, raw: str, reason: str) -> None:
        super().__init__(f"ruta rechazada {raw!r}: {reason}")
        self.raw = raw
        self.reason = reason


class NoteNotFoundError(VaultError):
    """La nota no existe."""


class NoteExistsError(VaultError):
    """Se intentó crear una nota que ya existe."""


class VersionConflictError(VaultError):
    """La nota cambió desde que se leyó (p. ej., el usuario la editó en Obsidian)."""


class VaultIOError(VaultError):
    """Fallo de entrada/salida al leer o escribir la bóveda."""


def _check_component(raw: str, part: str) -> None:
    if part in {"", ".", ".."}:
        raise PathRejectedError(raw, "componente vacío, '.' o '..'")
    if part.startswith("."):
        raise PathRejectedError(raw, "los archivos y carpetas ocultos no son notas")
    if len(part) > MAX_COMPONENT_LENGTH:
        raise PathRejectedError(raw, f"componente de más de {MAX_COMPONENT_LENGTH} caracteres")
    if _FORBIDDEN_CHARS.search(part):
        raise PathRejectedError(raw, 'carácter no permitido (\\, :, *, ?, ", <, >, | o control)')
    if any(c != " " and unicodedata.category(c) in _INVISIBLE_CATEGORIES for c in part):
        raise PathRejectedError(raw, "contiene caracteres invisibles o separadores especiales")
    if _SHORT_NAME_RE.search(part):
        raise PathRejectedError(raw, "parece un alias corto 8.3 de Windows (~N)")
    if part[-1] in {".", " "}:
        raise PathRejectedError(raw, "un componente no puede terminar en punto o espacio")
    # NFKC convierte variantes visuales (CON de ancho completo, COM con superíndice)
    # en el nombre reservado real.
    stem = unicodedata.normalize("NFKC", part.split(".", 1)[0]).lower()
    if stem in _RESERVED_NAMES:
        raise PathRejectedError(raw, "nombre reservado de Windows")


@dataclass(frozen=True, slots=True)
class VaultPath:
    """Ruta relativa a la raíz de la bóveda, con '/' como separador y sin escapes posibles."""

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise PathRejectedError(self.value, "ruta vacía")
        if self.value.startswith("/"):
            raise PathRejectedError(self.value, "ruta absoluta")
        if len(self.value) > MAX_PATH_LENGTH:
            raise PathRejectedError(self.value[:40], f"ruta de más de {MAX_PATH_LENGTH} caracteres")
        for part in self.value.split("/"):
            _check_component(self.value, part)

    @classmethod
    def parse(cls, raw: str) -> "VaultPath":
        return cls(raw)

    @property
    def parts(self) -> tuple[str, ...]:
        return tuple(self.value.split("/"))

    @property
    def name(self) -> str:
        return self.parts[-1]

    @property
    def parent(self) -> "VaultPath | None":
        parts = self.parts
        return VaultPath("/".join(parts[:-1])) if len(parts) > 1 else None

    def join(self, other: "VaultPath") -> "VaultPath":
        return VaultPath(f"{self.value}/{other.value}")

    def is_under(self, prefix: "VaultPath") -> bool:
        return self.parts[: len(prefix.parts)] == prefix.parts and self != prefix

    def relative_to(self, prefix: "VaultPath") -> "VaultPath":
        if not self.is_under(prefix):
            raise PathRejectedError(self.value, f"no está dentro de {prefix.value!r}")
        return VaultPath("/".join(self.parts[len(prefix.parts) :]))

    def __str__(self) -> str:
        return self.value


def _folded(path: VaultPath) -> tuple[str, ...]:
    # Windows y macOS no distinguen mayúsculas ni la forma Unicode (NFC/NFD) de los nombres.
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def within_any(path: VaultPath, roots: Iterable[VaultPath]) -> bool:
    """`path` es alguna de las raíces o está dentro de ella, comparando componentes completos."""
    parts = _folded(path)
    return any(parts[: len(root.parts)] == _folded(root) for root in roots)


@dataclass(frozen=True, slots=True)
class Version:
    """Huella del contenido leído; permite detectar cambios concurrentes."""

    digest: str

    def __post_init__(self) -> None:
        if not _SHA256_HEX.fullmatch(self.digest):
            raise ValueError("la versión debe ser un SHA-256 en hexadecimal")


@dataclass(frozen=True, slots=True)
class Snapshot:
    path: VaultPath
    text: str
    version: Version


class VaultStore(Protocol):
    """Acceso a notas Markdown. Toda escritura declara qué versión espera reemplazar."""

    def read(self, path: VaultPath) -> Snapshot: ...

    def exists(self, path: VaultPath) -> bool: ...

    def write(self, path: VaultPath, text: str, *, expected: Version | None) -> Version:
        """Crea la nota si `expected` es None; si no, la reemplaza solo si sigue en `expected`."""
        ...

    def list_notes(self, prefix: VaultPath | None = None) -> tuple[VaultPath, ...]: ...
