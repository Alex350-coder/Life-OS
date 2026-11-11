"""Candado de instancia única del servicio (ADR-0014): dos servicios enviarían todo dos veces.

Usa el bloqueo del sistema operativo sobre un archivo, que se libera solo si el proceso muere.
"""

import os
import sys
import time
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


class LockTimeoutError(LifeOSError):
    """Otro proceso (la CLI o la web) tiene el registro ocupado más tiempo del razonable."""


LOCK_POLL_S = 0.05


class FileLock(ProcessLock):
    """Exclusión entre procesos que espera su turno (registro de agentes y propuestas, Fase 8).

    La CLI y la web son procesos distintos: el candado en memoria de la web no las coordina.
    """

    def __init__(self, path: Path, *, timeout_s: float = 10.0) -> None:
        super().__init__(path)
        self._timeout_s = timeout_s

    def acquire(self) -> None:
        deadline = time.monotonic() + self._timeout_s
        while True:
            try:
                super().acquire()
                return
            except AlreadyRunningError:
                if time.monotonic() >= deadline:
                    raise LockTimeoutError(
                        f"el registro está ocupado por otro proceso ({self._path.name}); repite"
                    ) from None
                time.sleep(LOCK_POLL_S)
