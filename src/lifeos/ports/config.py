"""Configuración declarativa ya validada: política, catálogo de modelos y agentes (ADR-0004).

Los adaptadores convierten los archivos a estos tipos; el resto del sistema no conoce YAML ni
Pydantic. Un archivo de agente solo *solicita*: los permisos efectivos se calculan en
`application.permissions`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from lifeos.domain.errors import LifeOSError
from lifeos.ports.vault import VaultPath


class WriteMode(StrEnum):
    CREAR = "crear"  # crear notas nuevas; nunca editar las existentes
    ANEXAR_SECCION = "anexar_seccion"  # solo insertar líneas dentro de una sección concreta
    PLAN = "plan"  # publicar el plan validado; solo el dueño del plan global (Fase 3)


@dataclass(frozen=True, slots=True)
class WriteGrant:
    root: VaultPath
    mode: WriteMode
    section: str | None = None

    def __post_init__(self) -> None:
        if (self.mode is WriteMode.ANEXAR_SECCION) != (self.section is not None):
            raise ValueError("'section' es obligatoria con anexar_seccion y solo con ese modo")


@dataclass(frozen=True, slots=True)
class AgentCeiling:
    """Techo de permisos que el propietario concede a un agente."""

    reads: tuple[VaultPath, ...]
    writes: tuple[WriteGrant, ...]
    tools: frozenset[str]


@dataclass(frozen=True, slots=True)
class Policy:
    forbidden_write: tuple[VaultPath, ...]
    restricted_read: tuple[VaultPath, ...]
    plan_owner: str
    agents: Mapping[str, AgentCeiling]


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    name: str
    base_url: str
    api_key_env: str  # nombre de la variable de entorno, nunca el valor


@dataclass(frozen=True, slots=True)
class Profile:
    name: str
    provider: str
    model: str
    timeout_s: float
    max_tokens: int


@dataclass(frozen=True, slots=True)
class Tariff:
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal


@dataclass(frozen=True, slots=True)
class ModelCatalog:
    providers: Mapping[str, ProviderConfig]
    profiles: Mapping[str, Profile]
    tariff_date: date | None
    tariffs: Mapping[str, Tariff]


@dataclass(frozen=True, slots=True)
class AgentConfig:
    id: str
    name: str
    version: int
    purpose: str
    profile: str
    contract_in: str
    contract_out: str
    tools: tuple[str, ...]
    reads: tuple[VaultPath, ...]
    writes: tuple[WriteGrant, ...]
    metrics: tuple[str, ...]
    source_sha256: str  # huella del archivo importado (registro-de-agentes)


class ConfigError(LifeOSError):
    """Configuración no utilizable. `violations` enumera todos los problemas encontrados."""

    def __init__(self, source: str, violations: tuple[str, ...]) -> None:
        super().__init__(f"{source}: " + "; ".join(violations))
        self.source = source
        self.violations = violations


class ConfigSyntaxError(ConfigError):
    """El archivo no es dato plano válido o no cumple el esquema (SEC-06)."""


class ConfigRejectedError(ConfigError):
    """La configuración solicita más de lo que la política permite (SEC-05)."""


class ConfigSource(Protocol):
    def load_policy(self) -> Policy: ...

    def load_catalog(self) -> ModelCatalog: ...

    def load_agents(self) -> tuple[AgentConfig, ...]: ...
