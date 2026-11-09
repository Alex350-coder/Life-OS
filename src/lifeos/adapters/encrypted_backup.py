"""Respaldo cifrado de la bóveda (SEC-14, ADR-0016).

Formato del archivo `.lorespaldo`: `MAGIC | sal (16) | nonce (12) | AES-256-GCM(zip)`. La clave sale
de la frase del propietario con scrypt; la cabecera va como dato autenticado, así que cualquier
cambio en el archivo (o una frase incorrecta) se detecta al descifrar. La frase no se guarda en
ningún sitio: si se pierde, el respaldo no se puede abrir.
"""

import io
import os
import secrets
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from lifeos.ports.backup import BackupError, BackupInfo

MAGIC = b"LIFEOS-RESPALDO1"
SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
SCRYPT_N = 2**17  # ~128 MiB de memoria: caro de atacar por fuerza bruta, ~0,5 s en este PC
SCRYPT_R, SCRYPT_P = 8, 1
MIN_PASSPHRASE_CHARS = 12
MAX_CONTENT_BYTES = 1024 * 2**20  # la bóveda se comprime en memoria
SUFFIX = ".lorespaldo"
SKIPPED_DIRS = frozenset({".trash", ".git"})
TEMP_PREFIX = ".lifeos-"


def _check_passphrase(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE_CHARS:
        raise BackupError(f"la frase debe tener al menos {MIN_PASSPHRASE_CHARS} caracteres")


def _files(source: Path) -> list[Path]:
    found: list[Path] = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in SKIPPED_DIRS for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file() or path.name.startswith(TEMP_PREFIX):
            continue  # un enlace podría sacar del respaldo archivos de fuera de la bóveda
        found.append(path)
    return found


def _safe_member(name: str) -> PurePosixPath:
    member = PurePosixPath(name)
    if (
        not name
        or member.is_absolute()
        or ".." in member.parts
        or ":" in name
        or "\\" in name
        or name.startswith("/")
    ):
        raise BackupError(f"el respaldo contiene una ruta no válida: {name!r}")
    return member


class EncryptedVaultBackup:
    def __init__(self, *, scrypt_n: int = SCRYPT_N) -> None:
        self._n = scrypt_n

    def _key(self, passphrase: str, salt: bytes) -> bytes:
        kdf = Scrypt(salt=salt, length=KEY_BYTES, n=self._n, r=SCRYPT_R, p=SCRYPT_P)
        return kdf.derive(passphrase.encode("utf-8"))

    # --- Crear -------------------------------------------------------------------------------

    def create(self, source: Path, target_dir: Path, passphrase: str, now: datetime) -> BackupInfo:
        _check_passphrase(passphrase)
        if not source.is_dir():
            raise BackupError("la bóveda no existe o no es una carpeta")
        source = source.resolve()
        target_dir = target_dir.resolve()
        if target_dir.is_relative_to(source):
            raise BackupError("el destino no puede estar dentro de la bóveda")
        content, files, size = self._zip(source)
        salt, nonce = secrets.token_bytes(SALT_BYTES), secrets.token_bytes(NONCE_BYTES)
        sealed = AESGCM(self._key(passphrase, salt)).encrypt(nonce, content, MAGIC + salt)
        path = target_dir / f"lifeos-{now:%Y%m%d-%H%M%S}{SUFFIX}"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            with path.open("xb") as handle:  # nunca sobrescribe un respaldo anterior
                handle.write(MAGIC + salt + nonce + sealed)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            raise BackupError(f"ya existe {path.name}; espera un segundo y repite") from None
        except OSError as error:
            raise BackupError(f"no se pudo escribir el respaldo ({error.strerror})") from None
        return BackupInfo(path, files, size)

    @staticmethod
    def _zip(source: Path) -> tuple[bytes, int, int]:
        buffer = io.BytesIO()
        total = 0
        files = _files(source)
        try:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for path in files:
                    total += path.stat().st_size
                    if total > MAX_CONTENT_BYTES:
                        raise BackupError("la bóveda supera 1 GiB: el respaldo en memoria no cabe")
                    archive.write(path, path.relative_to(source).as_posix())
        except OSError as error:
            raise BackupError(f"no se pudo leer la bóveda ({error.strerror})") from None
        return buffer.getvalue(), len(files), total

    # --- Abrir -------------------------------------------------------------------------------

    def _open(self, archive: Path, passphrase: str) -> zipfile.ZipFile:
        try:
            data = archive.read_bytes()
        except OSError as error:
            raise BackupError(f"no se pudo leer {archive.name} ({error.strerror})") from None
        header = len(MAGIC) + SALT_BYTES + NONCE_BYTES
        if len(data) <= header or not data.startswith(MAGIC):
            raise BackupError(f"{archive.name} no es un respaldo de Life OS")
        salt = data[len(MAGIC) : len(MAGIC) + SALT_BYTES]
        nonce = data[len(MAGIC) + SALT_BYTES : header]
        try:
            content = AESGCM(self._key(passphrase, salt)).decrypt(
                nonce, data[header:], MAGIC + salt
            )
        except InvalidTag:
            raise BackupError("frase incorrecta o respaldo dañado") from None
        try:
            zipped = zipfile.ZipFile(io.BytesIO(content))
            broken = zipped.testzip()
        except zipfile.BadZipFile:
            raise BackupError("el contenido del respaldo está dañado") from None
        if broken is not None:
            raise BackupError(f"el respaldo tiene un archivo dañado: {broken}")
        for name in zipped.namelist():
            _safe_member(name)
        return zipped

    def verify(self, archive: Path, passphrase: str) -> BackupInfo:
        with self._open(archive, passphrase) as zipped:
            infos = [i for i in zipped.infolist() if not i.is_dir()]
            return BackupInfo(archive, len(infos), sum(i.file_size for i in infos))

    def restore(self, archive: Path, passphrase: str, target: Path) -> BackupInfo:
        if target.exists() and (not target.is_dir() or any(target.iterdir())):
            raise BackupError("el destino debe no existir o estar vacío: nunca se sobrescribe")
        with self._open(archive, passphrase) as zipped:
            infos = [i for i in zipped.infolist() if not i.is_dir()]
            try:
                for info in infos:
                    destination = target.joinpath(*_safe_member(info.filename).parts)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(zipped.read(info))
            except OSError as error:
                raise BackupError(f"no se pudo restaurar ({error.strerror})") from None
        return BackupInfo(target, len(infos), sum(i.file_size for i in infos))
