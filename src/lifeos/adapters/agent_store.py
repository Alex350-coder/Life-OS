"""Registro de agentes en JSON local (ADR-0018): `registro.json` más `<id>/v<N>.yaml`.

Vive en la carpeta de estado del proyecto, fuera de Git y de la bóveda. Escritura atómica; un
registro dañado es un error explícito, nunca un registro vacío (borrarlo «activaría» de nuevo
el estado inicial sin que el propietario lo sepa).
"""

import json
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from lifeos.domain.agent_lifecycle import (
    AGENT_ID_RE,
    AgentState,
    AgentVersion,
    LifecycleAction,
    LifecycleError,
    RegistryEntry,
    Transition,
)
from lifeos.ports.state import StateError

REGISTRY_FILE = "registro.json"
FORMAT_VERSION = 1
MAX_REGISTRY_BYTES = 2 * 1024 * 1024
_SAFE_ID = re.compile(AGENT_ID_RE.pattern)
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _moment(raw: object) -> datetime:
    moment = datetime.fromisoformat(str(raw))
    if moment.utcoffset() is None:
        raise ValueError("fecha sin zona horaria")
    return moment


def _positive_int(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 1:
        raise ValueError("entero no válido")
    return raw


def _sha256(raw: object) -> str:
    if not isinstance(raw, str) or not _SHA256.fullmatch(raw):
        raise ValueError("huella no válida")
    return raw


def _approved(raw: Any, number: int, legacy: frozenset[int]) -> bool:
    """`approved` explícito, o deducido del historial en un registro anterior (DT-28)."""
    if "approved" not in raw:
        return number in legacy
    value = raw["approved"]
    if not isinstance(value, bool):
        raise ValueError("'approved' debe ser true o false")
    return value


def _version(raw: Any, legacy: frozenset[int]) -> AgentVersion:
    number = _positive_int(raw["number"])
    return AgentVersion(
        number=number,
        sha256=_sha256(raw["sha256"]),
        config_version=_positive_int(raw["config_version"]),
        imported_at=_moment(raw["imported_at"]),
        approved=_approved(raw, number, legacy),
    )


def _transition(raw: Any) -> Transition:
    return Transition(
        at=_moment(raw["at"]),
        action=LifecycleAction(raw["action"]),
        to_state=AgentState(raw["to_state"]),
        version=_positive_int(raw["version"]),
    )


def _entry(key: str, raw: Any) -> RegistryEntry:
    history = tuple(_transition(item) for item in raw["history"])
    legacy = frozenset(t.version for t in history if t.action is LifecycleAction.APROBAR)
    versions = tuple(_version(item, legacy) for item in raw["versions"])
    entry = RegistryEntry(
        agent_id=str(raw["agent_id"]),
        state=AgentState(raw["state"]),
        current=_positive_int(raw["current"]),
        versions=versions,
        history=history,
    )
    if entry.agent_id != key or not _SAFE_ID.fullmatch(key):
        raise ValueError("id incoherente")
    if [v.number for v in versions] != list(range(1, len(versions) + 1)):
        raise ValueError("versiones no consecutivas")
    if not 1 <= entry.current <= len(versions):
        raise ValueError("versión actual inexistente")
    return entry


def _dump(entry: RegistryEntry) -> dict[str, object]:
    return {
        "agent_id": entry.agent_id,
        "state": entry.state.value,
        "current": entry.current,
        "versions": [
            {
                "number": v.number,
                "sha256": v.sha256,
                "config_version": v.config_version,
                "imported_at": v.imported_at.isoformat(),
                "approved": v.approved,
            }
            for v in entry.versions
        ],
        "history": [
            {
                "at": t.at.isoformat(),
                "action": t.action.value,
                "to_state": t.to_state.value,
                "version": t.version,
            }
            for t in entry.history
        ],
    }


def _write_atomic(path: Path, data: bytes) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            Path(temp).replace(path)
        except BaseException:
            Path(temp).unlink(missing_ok=True)
            raise
    except OSError as error:
        raise StateError(f"no se pudo guardar {path.name}: {type(error).__name__}") from None


class JsonAgentRegistry:
    def __init__(self, directory: Path) -> None:
        self._dir = directory

    @property
    def _registry(self) -> Path:
        return self._dir / REGISTRY_FILE

    def _read_raw(self) -> dict[str, Any]:
        try:
            data = self._registry.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as error:
            raise StateError(f"no se pudo leer {REGISTRY_FILE}: {type(error).__name__}") from None
        if len(data) > MAX_REGISTRY_BYTES:
            raise StateError(f"{REGISTRY_FILE} supera {MAX_REGISTRY_BYTES} bytes")
        try:
            parsed = json.loads(data.decode("utf-8"))
            if not isinstance(parsed, dict) or parsed.get("version") != FORMAT_VERSION:
                raise ValueError("formato desconocido")
            agents = parsed.get("agentes")
            if not isinstance(agents, dict):
                raise ValueError("falta 'agentes'")
            return agents
        except (ValueError, UnicodeDecodeError) as error:
            raise StateError(f"{REGISTRY_FILE} está dañado ({error}); revísalo") from None

    def load(self) -> Mapping[str, RegistryEntry]:
        raw = self._read_raw()
        try:
            return {key: _entry(key, value) for key, value in raw.items()}
        except (KeyError, TypeError, ValueError, LifecycleError) as error:
            raise StateError(
                f"{REGISTRY_FILE} está dañado ({type(error).__name__}); revísalo"
            ) from None

    def save(self, entry: RegistryEntry) -> None:
        agents = {key: value for key, value in self._read_raw().items() if key != entry.agent_id}
        agents[entry.agent_id] = _dump(entry)
        payload = {"version": FORMAT_VERSION, "agentes": dict(sorted(agents.items()))}
        text = json.dumps(payload, ensure_ascii=False, indent=1) + "\n"
        _write_atomic(self._registry, text.encode("utf-8"))

    def _version_path(self, agent_id: str, number: int) -> Path:
        if not _SAFE_ID.fullmatch(agent_id) or isinstance(number, bool) or number < 1:
            raise StateError("id de agente o número de versión no válidos")
        return self._dir / agent_id / f"v{number}.yaml"

    def put_version(
        self, agent_id: str, number: int, data: bytes, *, replace: bool = False
    ) -> None:
        path = self._version_path(agent_id, number)
        if path.exists() and not replace:
            try:
                same = path.read_bytes() == data
            except OSError as error:
                raise StateError(f"no se pudo leer la v{number} de {agent_id}") from error
            if same:
                return
            raise StateError(f"la v{number} de {agent_id} ya existe con otro contenido")
        _write_atomic(path, data)

    def get_version(self, agent_id: str, number: int) -> bytes:
        path = self._version_path(agent_id, number)
        try:
            return path.read_bytes()
        except FileNotFoundError:
            raise StateError(f"no se encontró la v{number} de {agent_id}") from None
        except OSError as error:
            raise StateError(
                f"no se pudo leer la v{number} de {agent_id}: {error.strerror}"
            ) from None
