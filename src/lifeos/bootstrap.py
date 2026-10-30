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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.jsonl_logs import JsonlAuditLog, JsonlUsageLog, JsonlUsageReader
from lifeos.adapters.nvidia_llm import NvidiaChatClient
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.secrets import MissingSecretError, Secret, read_dotenv, resolve_secret
from lifeos.adapters.system_clock import SystemClock
from lifeos.adapters.yaml_config import YamlConfigSource
from lifeos.application.closing import DailyClosing
from lifeos.application.coach import CoachSession
from lifeos.application.coach_ai import AiCoachWriter
from lifeos.application.jefe import JefePlanner
from lifeos.application.jefe_ai import AiPlanDrafter
from lifeos.application.llm_calls import ModelGateway
from lifeos.application.permissions import (
    TOOL_WRITE_MODES,
    EffectivePermissions,
    protected_roots,
    resolve_effective,
)
from lifeos.application.tools import ToolRegistry
from lifeos.ports.clock import Clock
from lifeos.ports.config import ConfigError, ConfigRejectedError, ModelCatalog, Policy
from lifeos.ports.llm import LlmClient
from lifeos.ports.logs import AuditLog, UsageLog, UsageReader
from lifeos.ports.vault import VaultPath, VaultStore

ENV_FILE = ".env"
# Errores de arranque (configuración o secretos ausentes) que la presentación trata como error
# de configuración sin conocer los adaptadores.
STARTUP_ERRORS: tuple[type[Exception], ...] = (ConfigError, MissingSecretError)
CONFIG_DIR = "config"
DEFAULT_LOGS_DIR = "logs"


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    config_dir: Path
    logs_dir: Path
    vault_path: Path | None
    timezone: str | None  # LIFEOS_TIMEZONE: debe coincidir con la zona de horario.md
    # Entorno del proceso con `.env` como respaldo. Contiene secretos: nunca aparece en `repr`.
    environ: Mapping[str, str] = field(repr=False)

    @classmethod
    def from_env(cls, project_root: Path, environ: Mapping[str, str] | None = None) -> Self:
        process = dict(os.environ if environ is None else environ)
        try:
            dotenv = read_dotenv(project_root / ENV_FILE)
        except ValueError as error:  # el mensaje nombra el archivo y la línea, nunca valores
            raise ConfigRejectedError(ENV_FILE, (str(error),)) from None
        merged = {**dotenv, **{k: v for k, v in process.items() if v}}
        logs = merged.get("LIFEOS_LOGS_DIR") or str(project_root / DEFAULT_LOGS_DIR)
        vault = merged.get("LIFEOS_VAULT_PATH")
        return cls(
            project_root=project_root,
            config_dir=project_root / CONFIG_DIR,
            logs_dir=Path(logs),
            vault_path=Path(vault) if vault else None,
            timezone=merged.get("LIFEOS_TIMEZONE") or None,
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
    # Bóveda sin envolver para las órdenes del propietario (capturar, tareas); los agentes
    # solo acceden mediante `tools`.
    vault: VaultStore | None
    jefe: JefePlanner | None
    coach: CoachSession | None
    closing: DailyClosing | None  # cierre diario del propietario (con propuestas del Coach)
    usage_reader: UsageReader


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


def _check_timezone(settings: Settings) -> None:
    if settings.timezone is None:
        return
    try:
        ZoneInfo(settings.timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigRejectedError(
            "LIFEOS_TIMEZONE", (f"zona IANA desconocida: {settings.timezone}",)
        ) from None


def _jefe(
    tools: ToolRegistry | None,
    gateway: ModelGateway | None,
    permissions: Mapping[str, EffectivePermissions],
    clock: Clock,
    settings: Settings,
) -> JefePlanner | None:
    if tools is None or "jefe" not in permissions:
        return None
    drafter = (
        AiPlanDrafter(gateway, SCHEMAS["plan_v1"], profile=permissions["jefe"].profile)
        if gateway is not None
        else None
    )
    return JefePlanner(tools, clock, drafter=drafter, configured_zone=settings.timezone)


def _coach(
    tools: ToolRegistry | None,
    gateway: ModelGateway | None,
    permissions: Mapping[str, EffectivePermissions],
    clock: Clock,
    settings: Settings,
) -> CoachSession | None:
    if tools is None or "coach" not in permissions:
        return None
    writer = (
        AiCoachWriter(gateway, SCHEMAS["coach_v1"], profile=permissions["coach"].profile)
        if gateway is not None
        else None
    )
    return CoachSession(tools, clock, writer=writer, configured_zone=settings.timezone)


def build_runtime(
    settings: Settings, *, with_llm: bool = False, clock: Clock | None = None
) -> Runtime:
    _check_timezone(settings)
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
    # Los logs siguen en UTC (ADR-0008); el plan usa la zona de horario.md.
    clock = clock or SystemClock(UTC)
    gateway = (
        ModelGateway(_clients(catalog, secrets), catalog, clock, usage_log) if with_llm else None
    )
    vault = FilesystemVault(settings.vault_path) if settings.vault_path is not None else None
    tools = ToolRegistry(vault, permissions, audit_log, clock) if vault is not None else None
    coach = _coach(tools, gateway, permissions, clock, settings)
    closing = (
        DailyClosing(vault, coach, clock, audit_log, configured_zone=settings.timezone)
        if vault is not None and coach is not None
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
        vault=vault,
        jefe=_jefe(tools, gateway, permissions, clock, settings),
        coach=coach,
        closing=closing,
        usage_reader=JsonlUsageReader(settings.logs_dir),
    )
