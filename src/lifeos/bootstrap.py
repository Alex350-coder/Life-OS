"""Raíz de composición (ADR-0003, revisión 2026-10-27): único módulo que conecta adaptadores
con casos de uso. Ninguna capa lo importa (tests/test_architecture.py).

Valida toda la configuración al arrancar: si la política, el catálogo o algún agente no son
válidos, no se construye nada. La clave del proveedor solo se exige cuando se pide la pasarela
de modelos.
"""

import hashlib
import os
from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from types import MappingProxyType
from typing import Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.adapters.json_state import JsonRitualState
from lifeos.adapters.jsonl_logs import (
    JsonlAuditLog,
    JsonlAuditReader,
    JsonlUsageLog,
    JsonlUsageReader,
)
from lifeos.adapters.nvidia_llm import NvidiaChatClient
from lifeos.adapters.process_lock import ProcessLock
from lifeos.adapters.schemas import SCHEMAS
from lifeos.adapters.secrets import MissingSecretError, Secret, read_dotenv, resolve_secret
from lifeos.adapters.system_clock import SystemClock
from lifeos.adapters.telegram import DEFAULT_BASE_URL, TelegramBotClient, TelegramNotifier
from lifeos.adapters.whisper import MODEL_SIZES, FasterWhisperTranscriber
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
from lifeos.application.rituals import RitualRunner
from lifeos.application.tools import ToolRegistry
from lifeos.ports.chat import ChatChannel
from lifeos.ports.clock import Clock
from lifeos.ports.config import (
    AgentConfig,
    ConfigError,
    ConfigRejectedError,
    ModelCatalog,
    Policy,
)
from lifeos.ports.llm import LlmClient
from lifeos.ports.logs import AuditLog, AuditReader, UsageLog, UsageReader
from lifeos.ports.transcribe import Transcriber
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
    # Configuración validada de los agentes y lector de auditoría (vista de la web, F5-T04).
    agents: tuple[AgentConfig, ...]
    audit_reader: AuditReader


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
    settings: Settings,
    *,
    with_llm: bool = False,
    clock: Clock | None = None,
    extra_secrets: tuple[Secret, ...] = (),
) -> Runtime:
    _check_timezone(settings)
    source = YamlConfigSource(settings.config_dir)
    policy = source.load_policy()
    catalog = source.load_catalog()
    agents = source.load_agents()
    permissions = resolve_effective(
        policy,
        catalog,
        agents,
        tools=TOOL_WRITE_MODES,
        contracts=frozenset(SCHEMAS),
    )
    secrets = _provider_secrets(settings, catalog) if with_llm else {}
    # Toda clave conocida se redacta de los logs (SEC-08), incluido el token de Telegram.
    known = tuple(secret.reveal() for secret in (*secrets.values(), *extra_secrets))
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
        agents=agents,
        audit_reader=JsonlAuditReader(settings.logs_dir),
    )


# --- Servicio de rituales y Telegram (Fase 6, ADR-0014) --------------------------------------

TELEGRAM_TOKEN_ENV = "TELEGRAM_BOT_TOKEN"  # noqa: S105 - nombre de la variable, no el token
OWNER_CHAT_ENV = "TELEGRAM_OWNER_CHAT_ID"
TELEGRAM_API_ENV = "LIFEOS_TELEGRAM_API"  # solo para pruebas locales
STATE_DIR_ENV = "LIFEOS_STATE_DIR"
WHISPER_MODEL_ENV = "LIFEOS_WHISPER_MODEL"
DEFAULT_STATE_DIR = "state"
DEFAULT_WHISPER_MODEL = "small"
MAX_CHAT_ID_DIGITS = 20


@dataclass(frozen=True, slots=True)
class ServiceRuntime:
    runtime: Runtime
    vault: VaultStore
    coach: CoachSession
    channel: ChatChannel
    rituals: RitualRunner
    transcriber: Transcriber | None
    owner_chat_id: int
    fingerprint_key: bytes = field(repr=False)  # clave de la huella de SEC-12
    lock: AbstractContextManager[object] = field(repr=False)


def _telegram_token(settings: Settings) -> Secret:
    return resolve_secret(TELEGRAM_TOKEN_ENV, environ=settings.environ, dotenv={})


def _telegram_client(settings: Settings, token: Secret) -> TelegramBotClient:
    try:
        return TelegramBotClient(
            token, base_url=settings.environ.get(TELEGRAM_API_ENV) or DEFAULT_BASE_URL
        )
    except ValueError as error:
        raise ConfigRejectedError(TELEGRAM_TOKEN_ENV, (str(error),)) from None


def telegram_channel(settings: Settings) -> ChatChannel:
    """Solo el canal (para `lifeos-servicio --descubrir`): no exige la bóveda ni el chat_id."""
    return _telegram_client(settings, _telegram_token(settings))


def _owner_chat_id(settings: Settings) -> int:
    raw = (settings.environ.get(OWNER_CHAT_ENV) or "").strip()
    if not raw.isascii() or not raw.isdigit() or len(raw) > MAX_CHAT_ID_DIGITS or int(raw) <= 0:
        raise ConfigRejectedError(
            OWNER_CHAT_ENV,
            ("debe ser el número de tu chat (obtenlo con 'lifeos-servicio --descubrir')",),
        )
    return int(raw)


def _whisper_model(settings: Settings) -> str:
    model = settings.environ.get(WHISPER_MODEL_ENV) or DEFAULT_WHISPER_MODEL
    if model not in MODEL_SIZES:
        raise ConfigRejectedError(
            WHISPER_MODEL_ENV, (f"modelo desconocido; usa {', '.join(sorted(MODEL_SIZES))}",)
        )
    return model


def build_service(settings: Settings, *, clock: Clock | None = None) -> ServiceRuntime:
    if settings.vault_path is None:
        raise ConfigRejectedError("LIFEOS_VAULT_PATH", ("el servicio necesita la bóveda",))
    token = _telegram_token(settings)
    client = _telegram_client(settings, token)
    owner = _owner_chat_id(settings)
    model = _whisper_model(settings)
    runtime = build_runtime(settings, clock=clock, extra_secrets=(token,))
    vault, jefe, coach = runtime.vault, runtime.jefe, runtime.coach
    if vault is None or jefe is None or coach is None:
        raise ConfigRejectedError("agentes", ("el servicio necesita al Jefe y al Coach",))
    state_dir = Path(
        settings.environ.get(STATE_DIR_ENV) or settings.project_root / DEFAULT_STATE_DIR
    )
    rituals = RitualRunner(
        vault,
        jefe,
        coach,
        TelegramNotifier(client, chat_id=owner),
        JsonRitualState(state_dir / "rituales.json"),
        runtime.audit_log,
        runtime.clock,
        configured_zone=settings.timezone,
    )
    return ServiceRuntime(
        runtime=runtime,
        vault=vault,
        coach=coach,
        channel=client,
        rituals=rituals,
        transcriber=FasterWhisperTranscriber(model),
        owner_chat_id=owner,
        # Derivada del token: secreta, estable y sin otro secreto que guardar.
        fingerprint_key=hashlib.sha256(b"lifeos-sec12:" + token.reveal().encode()).digest(),
        lock=ProcessLock(state_dir / "servicio.lock"),
    )
