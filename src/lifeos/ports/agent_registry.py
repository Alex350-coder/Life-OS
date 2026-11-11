"""Almacén del registro de agentes importados (F8-T01, ADR-0018).

Guarda el estado del ciclo de vida y una copia inmutable de cada versión importada. El archivo
de una versión no se sobrescribe nunca: restaurar es volver a leerlo. Los errores de lectura o
escritura son `StateError` (un registro dañado nunca se reinicia en silencio).
"""

from collections.abc import Mapping
from typing import Protocol

from lifeos.domain.agent_lifecycle import RegistryEntry


class AgentRegistryStore(Protocol):
    def load(self) -> Mapping[str, RegistryEntry]: ...

    def save(self, entry: RegistryEntry) -> None: ...

    def put_version(self, agent_id: str, number: int, data: bytes) -> None:
        """Guarda la copia de una versión; si ya existe con otro contenido, `StateError`."""
        ...

    def get_version(self, agent_id: str, number: int) -> bytes: ...
