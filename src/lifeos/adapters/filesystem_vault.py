"""Bóveda Markdown en disco: confinada a su raíz (SEC-02) y con escritura atómica (SEC-03).

Limitación conocida: entre la comprobación de versión y `os.replace` queda una ventana muy
corta en la que otro proceso podría escribir; la biblioteca estándar no ofrece un bloqueo
fiable frente a Obsidian. Se documenta en PROGRESS.md.
"""

import hashlib
import os
import tempfile
import time
from pathlib import Path

from lifeos.ports.vault import (
    NoteExistsError,
    NoteNotFoundError,
    PathRejectedError,
    Snapshot,
    VaultIOError,
    VaultPath,
    Version,
    VersionConflictError,
)

NOTE_SUFFIX = ".md"
TEMP_PREFIX = ".lifeos-"
REPLACE_ATTEMPTS = 3
REPLACE_BACKOFF_SECONDS = 0.05


def _version_of(data: bytes) -> Version:
    return Version(hashlib.sha256(data).hexdigest())


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _is_listable(path: Path) -> bool:
    # Los enlaces se excluyen por SEC-02; los ocultos (.obsidian, temporales) no son notas.
    return not path.name.startswith(".") and not _is_link(path)


def _raise_walk_error(error: OSError) -> None:
    raise VaultIOError(f"no se pudo recorrer la bóveda: {error}") from error


def _discard(temp: Path | None) -> None:
    """Borra el temporal sin ocultar el error principal si el borrado también falla."""
    if temp is None:
        return
    try:
        temp.unlink(missing_ok=True)
    except OSError:
        # Queda un `.lifeos-*` huérfano: es inofensivo (oculto y fuera de list_notes).
        return


class FilesystemVault:
    """Implementación de `VaultStore` sobre un directorio local."""

    def __init__(self, root: Path) -> None:
        try:
            resolved = root.resolve(strict=True)
        except OSError as exc:
            raise VaultIOError(f"la raíz de la bóveda no existe: {root}") from exc
        if not resolved.is_dir():
            raise VaultIOError(f"la raíz de la bóveda no es un directorio: {root}")
        self._root = resolved

    # --- Confinamiento -------------------------------------------------------------------

    def _locate(self, path: VaultPath) -> Path:
        """Ruta absoluta segura: ningún componente es un enlace y no sale de la raíz."""
        current = self._root
        for part in path.parts:
            current = current / part
            if _is_link(current):
                raise PathRejectedError(path.value, "contiene un enlace simbólico o junction")
        if not current.resolve(strict=False).is_relative_to(self._root):
            raise PathRejectedError(path.value, "sale de la raíz de la bóveda")
        return current

    # --- Lectura -------------------------------------------------------------------------

    def _read_bytes(self, target: Path) -> bytes | None:
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise VaultIOError(f"no se pudo leer {target.name}: {exc}") from exc

    def read(self, path: VaultPath) -> Snapshot:
        data = self._read_bytes(self._locate(path))
        if data is None:
            raise NoteNotFoundError(path.value)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise VaultIOError(f"{path.value} no está codificada en UTF-8") from exc
        return Snapshot(path=path, text=text, version=_version_of(data))

    def exists(self, path: VaultPath) -> bool:
        return self._locate(path).is_file()

    def list_notes(self, prefix: VaultPath | None = None) -> tuple[VaultPath, ...]:
        base = self._locate(prefix) if prefix is not None else self._root
        if not base.is_dir():
            return ()
        found: list[VaultPath] = []
        # Una carpeta ilegible no se salta en silencio: faltarían tareas e ids ocupados.
        for directory, dirnames, filenames in os.walk(base, onerror=_raise_walk_error):
            current = Path(directory)
            dirnames[:] = sorted(d for d in dirnames if _is_listable(current / d))
            for filename in filenames:
                candidate = current / filename
                if filename.lower().endswith(NOTE_SUFFIX) and _is_listable(candidate):
                    found.append(self._to_vault_path(candidate))
        return tuple(sorted(found, key=lambda p: p.value))

    def _to_vault_path(self, candidate: Path) -> VaultPath:
        relative = candidate.relative_to(self._root).as_posix()
        try:
            return VaultPath(relative)
        except PathRejectedError as exc:
            raise VaultIOError(f"nombre de nota no admitido: {exc.reason}") from exc

    # --- Escritura atómica ---------------------------------------------------------------

    def write(self, path: VaultPath, text: str, *, expected: Version | None) -> Version:
        target = self._locate(path)
        data = text.encode("utf-8")
        self._ensure_parent(path, target)
        temp: Path | None = None
        try:
            fd, temp_name = tempfile.mkstemp(prefix=TEMP_PREFIX, dir=target.parent)
            temp = Path(temp_name)
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._check_version(path, target, expected)
            self._replace(temp, target)
        except OSError as exc:
            raise VaultIOError(f"no se pudo escribir {path.value}: {exc}") from exc
        finally:
            _discard(temp)
        return _version_of(data)

    def _ensure_parent(self, path: VaultPath, target: Path) -> None:
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise VaultIOError(f"no se pudo crear la carpeta de {path.value}: {exc}") from exc
        # Revalidar tras crear carpetas: ninguna puede haberse convertido en un enlace.
        self._locate(path)

    def _check_version(self, path: VaultPath, target: Path, expected: Version | None) -> None:
        current = self._read_bytes(target)
        if expected is None:
            if current is not None:
                raise NoteExistsError(path.value)
            return
        if current is None or _version_of(current) != expected:
            raise VersionConflictError(f"{path.value} cambió desde la última lectura")

    @staticmethod
    def _replace(temp: Path, target: Path) -> None:
        # En Windows, Obsidian, OneDrive o el antivirus pueden bloquear el archivo un instante.
        for attempt in range(1, REPLACE_ATTEMPTS + 1):
            try:
                os.replace(temp, target)  # noqa: PTH105 - punto único sustituible en pruebas
                return
            except PermissionError:
                if attempt == REPLACE_ATTEMPTS:
                    raise
                time.sleep(REPLACE_BACKOFF_SECONDS * attempt)
