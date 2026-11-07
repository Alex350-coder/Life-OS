"""Estado de los rituales en un JSON local (ADR-0014). Escritura atómica; un archivo corrupto
es un error explícito: reiniciarlo en silencio podría repetir avisos ya enviados."""

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

from lifeos.ports.state import RitualMark, RitualStatus, StateError


def _mark(name: str, raw: object) -> RitualMark:
    if not isinstance(raw, dict):
        raise ValueError("entrada no es un objeto")
    attempts = raw.get("attempts")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("intentos no válidos")
    updated = datetime.fromisoformat(str(raw.get("updated")))
    if updated.utcoffset() is None:
        raise ValueError("fecha sin zona horaria")
    reason = raw.get("reason")
    return RitualMark(
        RitualStatus(str(raw.get("status"))),
        attempts,
        updated,
        reason if isinstance(reason, str) else None,
    )


class JsonRitualState:
    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> dict[str, RitualMark]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise StateError(f"no se pudo leer {self._path.name}: {type(error).__name__}") from None
        try:
            data = json.loads(text)
            if not isinstance(data, dict):
                raise ValueError("no es un objeto")
            return {str(key): _mark(key, value) for key, value in data.items()}
        except (ValueError, TypeError) as error:
            raise StateError(
                f"{self._path.name} está dañado ({error}); revísalo o bórralo para empezar de cero"
            ) from None

    def save(self, marks: Mapping[str, RitualMark]) -> None:
        data = {
            key: {
                "status": mark.status.value,
                "attempts": mark.attempts,
                "updated": mark.updated.isoformat(),
                "reason": mark.reason,
            }
            for key, mark in sorted(marks.items())
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(dir=self._path.parent, prefix=".rituales-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(data, handle, ensure_ascii=False, indent=1)
                    handle.flush()
                    os.fsync(handle.fileno())
                Path(temp).replace(self._path)
            except BaseException:
                Path(temp).unlink(missing_ok=True)
                raise
        except OSError as error:
            raise StateError(
                f"no se pudo guardar {self._path.name}: {type(error).__name__}"
            ) from None
