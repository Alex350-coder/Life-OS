"""Candado de instancia única del servicio (ADR-0014): dos servicios enviarían todo dos veces.

Usa el bloqueo del sistema operativo sobre un archivo, que se libera solo si el proceso muere.
"""

import os
import sys
from pathlib import Path
from types import TracebackType
from typing import Self

from lifeos.domain.errors import LifeOSError


class AlreadyRunningError(LifeOSError):
    """Ya hay otro `lifeos-servicio` en marcha con el mismo directorio de estado."""


def _try_lock(fd: int) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


class ProcessLock:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._fd: int | None = None

    def acquire(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o600)
        if not _try_lock(fd):
            os.close(fd)
            raise AlreadyRunningError(f"ya hay un servicio en marcha ({self._path.name})")
        self._fd = fd

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # cerrar el descriptor libera el bloqueo
            self._fd = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.release()
