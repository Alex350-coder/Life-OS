"""Raíz de composición (ADR-0003, revisión 2026-10-27): único módulo que conecta adaptadores
con casos de uso. Ninguna capa lo importa (tests/test_architecture.py).

Valida toda la configuración al arrancar: si la política, el catálogo o algún agente no son
válidos, no se construye nada. La clave del proveedor solo se exige cuando se pide la pasarela
de modelos.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from types import MappingProxyType
from typing import Self

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.jsonl_logs import JsonlAuditLog, JsonlUsageLog
from lifeos.adapters.nvidia_llm import NvidiaChatClient
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.secrets import Secret, read_dotenv, resolve_secret
from lifeos.adapters.system_clock import SystemClock
from lifeos.adapters.yaml_config import YamlConfigSource
from lifeos.application.llm_calls import ModelGateway
from lifeos.application.permissions import (
    TOOL_WRITE_MODES,
    EffectivePermissions,
    protected_roots,
    resolve_effective,
)
from lifeos.application.tools import ToolRegistry
from lifeos.ports.clock import Clock
from lifeos.ports.config import ConfigRejectedError, ModelCatalog, Policy
from lifeos.ports.llm import LlmClient
from lifeos.ports.logs import AuditLog, UsageLog
from lifeos.ports.vault import VaultPath

ENV_FILE = ".env"
CONFIG_DIR = "config"
DEFAULT_LOGS_DIR = "logs"


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    config_dir: Path
    logs_dir: Path
    vault_path: Path | None
    # Entorno del proceso con `.env` como respaldo. Contiene secretos: nunca aparece en `repr`.
    environ: Mapping[str, str] = field(repr=False)

    @classmethod
    def from_env(cls, project_root: Path, environ: Mapping[str, str] | None = None) -> Self:
        process = dict(os.environ if environ is None else environ)
        merged = {**read_dotenv(project_root / ENV_FILE), **{k: v for k, v in process.items() if v}}
        logs = merged.get("LIFEOS_LOGS_DIR") or str(project_root / DEFAULT_LOGS_DIR)
        vault = merged.get("LIFEOS_VAULT_PATH")
        return cls(
            project_root=project_root,
            config_dir=project_root / CONFIG_DIR,
            logs_dir=Path(logs),
            vault_path=Path(vault) if vault else None,
            environ=MappingProxyType(merged),
        )


@dataclass(frozen=True, slots=True)
class Runtime:
    policy: Policy
    catalog: ModelCatalog
    permissions: Mapping[str, EffectivePermissions]
    protected: tuple[VaultPath, ...]
    usage_log: UsageLog
    audit_log: AuditLog
    clock: Clock
    gateway: ModelGateway | None
    tools: ToolRegistry | None


def _provider_secrets(settings: Settings, catalog: ModelCatalog) -> dict[str, Secret]:
    return {
        name: resolve_secret(provider.api_key_env, environ=settings.environ, dotenv={})
        for name, provider in catalog.providers.items()
    }


def _clients(catalog: ModelCatalog, secrets: Mapping[str, Secret]) -> dict[str, LlmClient]:
    unsupported = sorted(set(catalog.providers) - {"nvidia"})
    if unsupported:
        raise ConfigRejectedError(
            "models.yaml", tuple(f"proveedor sin adaptador: {n}" for n in unsupported)
        )
    nvidia = catalog.providers["nvidia"]
    return {"nvidia": NvidiaChatClient(nvidia.base_url, secrets["nvidia"])}


def build_runtime(settings: Settings, *, with_llm: bool = False) -> Runtime:
    source = YamlConfigSource(settings.config_dir)
    policy = source.load_policy()
    catalog = source.load_catalog()
    permissions = resolve_effective(
        policy,
        catalog,
        source.load_agents(),
        tools=TOOL_WRITE_MODES,
        contracts=frozenset(SCHEMAS),
    )
    secrets = _provider_secrets(settings, catalog) if with_llm else {}
    known = tuple(secret.reveal() for secret in secrets.values())
    usage_log = JsonlUsageLog(settings.logs_dir, secrets=known)
    audit_log = JsonlAuditLog(settings.logs_dir, secrets=known)
    # La Fase 2 registra todo en UTC; la zona local (LIFEOS_TIMEZONE) llega con los rituales.
    clock = SystemClock(UTC)
    gateway = (
        ModelGateway(_clients(catalog, secrets), catalog, clock, usage_log) if with_llm else None
    )
    tools = (
        ToolRegistry(FilesystemVault(settings.vault_path), permissions, audit_log, clock)
        if settings.vault_path is not None
        else None
    )
    return Runtime(
        policy=policy,
        catalog=catalog,
        permissions=permissions,
        protected=protected_roots(policy),
        usage_log=usage_log,
        audit_log=audit_log,
        clock=clock,
        gateway=gateway,
        tools=tools,
    )
