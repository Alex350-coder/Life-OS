"""Agentes en solo lectura para la web (F5-T04, PLAN §9): estado, configuración, permisos
efectivos y métricas de uso del mes.

Los permisos que se muestran son los efectivos (solicitado ∩ política, ADR-0004), no lo que pide
el archivo del agente. No hay edición: el ciclo de vida llega en la Fase 8.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from lifeos.application.permissions import EffectivePermissions
from lifeos.application.tasks import ApplicationError
from lifeos.application.usage_report import UsageRow, UsageSummary, combine
from lifeos.ports.config import AgentConfig, ModelCatalog, Policy, WriteGrant
from lifeos.ports.vault import VaultPath

# Todos los agentes que pasan la validación quedan activos hasta la Fase 8 (RULES E.10).
ACTIVE = "activo"
ALL_RITUALS = "todos"


@dataclass(frozen=True, slots=True)
class GrantView:
    root: str
    mode: str
    section: str | None


@dataclass(frozen=True, slots=True)
class AgentOverview:
    id: str
    name: str
    version: int
    purpose: str
    status: str
    profile: str
    provider: str
    model: str
    contract_in: str
    contract_out: str
    reads: tuple[str, ...]
    writes: tuple[GrantView, ...]
    plan_writes: tuple[GrantView, ...]
    tools: tuple[str, ...]
    metrics: tuple[str, ...]
    usage: UsageRow | None  # None: sin llamadas al modelo en el mes


@dataclass(frozen=True, slots=True)
class AgentsOverview:
    agents: tuple[AgentOverview, ...]
    plan_owner: str
    forbidden_write: tuple[str, ...]
    restricted_read: tuple[str, ...]
    usage_malformed: int


def _root(path: VaultPath) -> str:
    return f"{path}/"


def _grants(grants: Sequence[WriteGrant]) -> tuple[GrantView, ...]:
    return tuple(GrantView(_root(g.root), str(g.mode), g.section) for g in grants)


def _agent_usage(agent_id: str, summary: UsageSummary) -> UsageRow | None:
    rows = [row for row in summary.rows if row.agent == agent_id]
    return combine(rows, agent=agent_id, ritual=ALL_RITUALS) if rows else None


def agents_overview(
    configs: Sequence[AgentConfig],
    permissions: Mapping[str, EffectivePermissions],
    policy: Policy,
    catalog: ModelCatalog,
    usage: UsageSummary,
) -> AgentsOverview:
    agents = []
    for config in sorted(configs, key=lambda c: c.id):
        effective = permissions.get(config.id)
        if effective is None:  # resolve_effective valida todos o ninguno: es un fallo de cableado
            raise ApplicationError(f"el agente {config.id} no tiene permisos efectivos validados")
        profile = catalog.profiles[effective.profile]
        agents.append(
            AgentOverview(
                id=config.id,
                name=config.name,
                version=config.version,
                purpose=config.purpose,
                status=ACTIVE,
                profile=effective.profile,
                provider=profile.provider,
                model=profile.model,
                contract_in=config.contract_in,
                contract_out=config.contract_out,
                reads=tuple(_root(path) for path in effective.reads),
                writes=_grants(effective.writes),
                plan_writes=_grants(effective.plan_writes),
                tools=tuple(sorted(effective.tools)),
                metrics=config.metrics,
                usage=_agent_usage(config.id, usage),
            )
        )
    return AgentsOverview(
        agents=tuple(agents),
        plan_owner=policy.plan_owner,
        forbidden_write=tuple(_root(path) for path in policy.forbidden_write),
        restricted_read=tuple(_root(path) for path in policy.restricted_read),
        usage_malformed=usage.malformed,
    )
