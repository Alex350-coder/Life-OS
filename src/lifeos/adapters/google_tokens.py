"""Tokens de Google en el estado local (ADR-0017, SEC-13).

Un archivo por permiso: `google-lectura.json` (freebusy) y `google-escritura.json`
(app.created), en la carpeta de estado (ignorada por Git). Solo se guarda el refresh token, el
scope concedido y la fecha; el access token vive en memoria. El id del calendario propio de
Life OS (no es secreto) va en `google-calendario.json`. Escritura atómica; un archivo dañado es
un error explícito.
"""

import json
import os
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from lifeos.adapters.secrets import Secret
from lifeos.ports.calendar import CalendarError

READ_SCOPE = "https://www.googleapis.com/auth/calendar.freebusy"
WRITE_SCOPE = "https://www.googleapis.com/auth/calendar.app.created"
CALENDAR_FILE = "google-calendario.json"
MAX_TOKEN_FILE_BYTES = 16 * 1024


class Grant(StrEnum):
    LECTURA = "lectura"
    ESCRITURA = "escritura"

    @property
    def scope(self) -> str:
        return READ_SCOPE if self is Grant.LECTURA else WRITE_SCOPE


@dataclass(frozen=True, slots=True)
class StoredToken:
    grant: Grant
    refresh_token: Secret = field(repr=False)
    scope: str
    obtained: datetime

    def __post_init__(self) -> None:
        if self.scope != self.grant.scope:
            raise CalendarError(f"el token de {self.grant} no tiene el scope esperado")
        if self.obtained.utcoffset() is None:
            raise CalendarError("la fecha del token requiere zona horaria")


def _write_private(path: Path, data: object) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                Path(temp).chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600 donde el sistema lo respeta
                json.dump(data, handle, ensure_ascii=False, indent=1)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temp).replace(path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
    except OSError as error:
        raise CalendarError(f"no se pudo guardar {path.name}: {type(error).__name__}") from None


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise CalendarError(f"no se pudo leer {path.name}: {type(error).__name__}") from None
    try:
        if len(raw) > MAX_TOKEN_FILE_BYTES:
            raise ValueError("demasiado grande")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("no es un objeto")
    except (ValueError, UnicodeDecodeError) as error:
        raise CalendarError(
            f"{path.name} está dañado ({error}); bórralo y vuelve a conectar"
        ) from None
    return data


class TokenStore:
    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir

    def _path(self, grant: Grant) -> Path:
        return self._dir / f"google-{grant.value}.json"

    def has(self, grant: Grant) -> bool:
        return self._path(grant).is_file()

    def load(self, grant: Grant) -> StoredToken | None:
        path = self._path(grant)
        data = _read_json(path)
        if data is None:
            return None
        try:
            token = data["refresh_token"]
            scope = data["scope"]
            if data.get("grant") != grant.value or not isinstance(token, str):
                raise ValueError("permiso o token no válidos")
            if not isinstance(scope, str):
                raise ValueError("scope no válido")
            obtained = datetime.fromisoformat(str(data["obtained"]))
            return StoredToken(grant, Secret(token), scope, obtained)
        except (KeyError, ValueError, TypeError) as error:
            raise CalendarError(
                f"{path.name} está dañado ({type(error).__name__}); bórralo y vuelve a conectar"
            ) from None

    def save(self, token: StoredToken) -> None:
        _write_private(
            self._path(token.grant),
            {
                "grant": token.grant.value,
                "refresh_token": token.refresh_token.reveal(),
                "scope": token.scope,
                "obtained": token.obtained.isoformat(),
            },
        )

    def remove(self, grant: Grant) -> bool:
        try:
            self._path(grant).unlink()
        except FileNotFoundError:
            return False
        except OSError as error:
            raise CalendarError(
                f"no se pudo borrar {self._path(grant).name}: {type(error).__name__}"
            ) from None
        return True

    # --- Calendario propio de Life OS (no es secreto) ------------------------------------------

    def calendar_id(self) -> str | None:
        data = _read_json(self._dir / CALENDAR_FILE)
        if data is None:
            return None
        value = data.get("id")
        if not isinstance(value, str) or not value or len(value) > 1024:
            raise CalendarError(f"{CALENDAR_FILE} está dañado; bórralo y vuelve a publicar")
        return value

    def save_calendar_id(self, calendar_id: str) -> None:
        _write_private(self._dir / CALENDAR_FILE, {"id": calendar_id})

    def forget_calendar(self) -> None:
        try:
            (self._dir / CALENDAR_FILE).unlink(missing_ok=True)
        except OSError as error:
            raise CalendarError(
                f"no se pudo borrar {CALENDAR_FILE}: {type(error).__name__}"
            ) from None
