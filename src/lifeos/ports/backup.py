"""Puerto del respaldo cifrado de la bóveda (SEC-14, ADR-0016)."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from lifeos.domain.errors import LifeOSError


class BackupError(LifeOSError):
    """No se pudo crear, leer o restaurar un respaldo. El mensaje nunca incluye la frase."""


@dataclass(frozen=True, slots=True)
class BackupInfo:
    path: Path
    files: int
    size_bytes: int  # tamaño sin cifrar del contenido


class VaultBackup(Protocol):
    def create(self, source: Path, target_dir: Path, passphrase: str, now: datetime) -> BackupInfo:
        """Cifra toda la bóveda en un archivo nuevo dentro de `target_dir`."""
        ...

    def verify(self, archive: Path, passphrase: str) -> BackupInfo:
        """Descifra y comprueba la integridad sin escribir nada."""
        ...

    def restore(self, archive: Path, passphrase: str, target: Path) -> BackupInfo:
        """Restaura en `target`, que debe no existir o estar vacía. Nunca sobrescribe."""
        ...
