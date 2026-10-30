"""Permisos efectivos = solicitados ∩ política del propietario (ADR-0004, SEC-05).

No se recorta en silencio: si un agente solicita algo que la política no concede, se rechaza
la configuración completa y se enumeran todos los problemas. La propia política también se
valida: no puede rebajar el mínimo de RULES E.12 ni dar el modo `plan` a otro agente que no
sea el dueño del plan global.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from lifeos.application.tasks import DEFAULT_PROTECTED_ROOTS, RULES_FORBIDDEN_ROOTS
from lifeos.ports.config import (
    AgentCeiling,
    AgentConfig,
    ConfigRejectedError,
    ModelCatalog,
    Policy,
    WriteGrant,
    WriteMode,
)
from lifeos.ports.vault import VaultPath, within_any

# Herramientas implementadas en código y el modo de escritura que necesita cada una.
TOOL_WRITE_MODES: Mapping[str, WriteMode | None] = MappingProxyType(
    {
        "leer_nota": None,
        "listar_tareas": None,
        "crear_captura": WriteMode.CREAR,
        "anexar_checkin": WriteMode.ANEXAR_SECCION,
        "publicar_plan": WriteMode.PLAN,  # solo el dueño del plan global (ADR-0002, SEC-09)
    }
)
PUBLISH_TOOL = "publicar_plan"
_SOURCE = "configuración de agentes"
# RULES E.12: Salud y Finanzas sin lectura de agentes hasta su fase. La política puede ampliar
# las áreas restringidas, nunca quitar estas.
RULES_RESTRICTED_READ = (VaultPath("05-Salud"), VaultPath("06-Finanzas"))


@dataclass(frozen=True, slots=True)
class EffectivePermissions:
    agent_id: str
    profile: str
    reads: tuple[VaultPath, ...]
    writes: tuple[WriteGrant, ...]
    tools: frozenset[str]
    # Copia de las raíces prohibidas: la bóveda con permisos las vuelve a comprobar.
    forbidden_write: tuple[VaultPath, ...]
    # Concesiones `plan` de la política: solo el dueño del plan global con `publicar_plan`.
    # Van aparte de `writes` porque ningún archivo de agente puede solicitarlas.
    plan_writes: tuple[WriteGrant, ...] = ()


def _same_section(granted: str | None, requested: str | None) -> bool:
    if granted is None or requested is None:
        return granted is requested
    return granted.casefold() == requested.casefold()


def _covers(ceiling: WriteGrant, requested: WriteGrant) -> bool:
    return (
        ceiling.mode is requested.mode
        and within_any(requested.root, (ceiling.root,))
        and _same_section(ceiling.section, requested.section)
    )


# --- Validación de la política ----------------------------------------------------------------


def _check_policy_grant(name: str, grant: WriteGrant, policy: Policy) -> list[str]:
    where = f"{name}: escritura '{grant.root}' ({grant.mode})"
    problems = []
    if grant.mode is WriteMode.PLAN and name != policy.plan_owner:
        problems.append(f"{where}: solo el dueño del plan global puede tener modo plan")
    if within_any(grant.root, policy.forbidden_write):
        problems.append(f"{where}: está en una carpeta prohibida para agentes")
    if within_any(grant.root, policy.restricted_read):
        problems.append(f"{where}: está en un área restringida")
    return problems


def _check_ceiling(name: str, ceiling: AgentCeiling, policy: Policy) -> list[str]:
    problems = [
        f"{name}: herramienta desconocida '{tool}' en la política"
        for tool in sorted(ceiling.tools - TOOL_WRITE_MODES.keys())
    ]
    if PUBLISH_TOOL in ceiling.tools:
        if name != policy.plan_owner:
            problems.append(f"{name}: solo el dueño del plan global puede tener '{PUBLISH_TOOL}'")
        elif not _plan_grants(ceiling):
            problems.append(f"{name}: '{PUBLISH_TOOL}' requiere escritura en modo plan")
    problems += [
        f"{name}: lectura '{root}' está en un área restringida"
        for root in ceiling.reads
        if within_any(root, policy.restricted_read)
    ]
    for grant in ceiling.writes:
        problems += _check_policy_grant(name, grant, policy)
    return problems


def validate_policy(policy: Policy) -> list[str]:
    problems = [
        f"global: escritura_prohibida_para_agentes debe incluir '{root}' (RULES E.12)"
        for root in RULES_FORBIDDEN_ROOTS
        if not within_any(root, policy.forbidden_write)
    ]
    problems += [
        f"global: lectura_restringida debe incluir '{root}' (RULES E.12)"
        for root in RULES_RESTRICTED_READ
        if not within_any(root, policy.restricted_read)
    ]
    if policy.plan_owner not in policy.agents:
        problems.append("global: dueno_plan_global no es un agente declarado en la política")
    for name, ceiling in policy.agents.items():
        problems += _check_ceiling(name, ceiling, policy)
    return problems


# --- Validación de cada agente ----------------------------------------------------------------


def _check_reads(agent: AgentConfig, ceiling: AgentCeiling, policy: Policy) -> list[str]:
    problems = []
    for root in agent.reads:
        if within_any(root, policy.restricted_read):
            problems.append(f"{agent.id}: lectura '{root}' está en un área restringida")
        elif not within_any(root, ceiling.reads):
            problems.append(f"{agent.id}: lectura '{root}' excede la política")
    return problems


def _check_writes(agent: AgentConfig, ceiling: AgentCeiling, policy: Policy) -> list[str]:
    problems = []
    for grant in agent.writes:
        where = f"{agent.id}: escritura '{grant.root}' ({grant.mode})"
        if grant.mode is WriteMode.PLAN:
            problems.append(f"{where}: un agente no puede solicitar el modo plan")
        elif within_any(grant.root, policy.forbidden_write):
            problems.append(f"{where}: está en una carpeta prohibida para agentes")
        elif not any(_covers(allowed, grant) for allowed in ceiling.writes):
            problems.append(f"{where}: excede la política")
    return problems


def _plan_grants(ceiling: AgentCeiling) -> tuple[WriteGrant, ...]:
    return tuple(grant for grant in ceiling.writes if grant.mode is WriteMode.PLAN)


def _check_tools(
    agent: AgentConfig, ceiling: AgentCeiling, tools: Mapping[str, WriteMode | None]
) -> list[str]:
    problems = []
    for tool in agent.tools:
        if tool not in tools:
            problems.append(f"{agent.id}: herramienta desconocida '{tool}'")
            continue
        if tool not in ceiling.tools:
            problems.append(f"{agent.id}: herramienta '{tool}' no autorizada por la política")
        mode = tools[tool]
        if mode is WriteMode.PLAN:
            # El modo plan no se solicita: lo concede la política (validada en _check_ceiling).
            continue
        if mode is not None and not any(grant.mode is mode for grant in agent.writes):
            problems.append(f"{agent.id}: la herramienta '{tool}' requiere escritura '{mode}'")
    return problems


def _check_agent(
    agent: AgentConfig,
    policy: Policy,
    catalog: ModelCatalog,
    tools: Mapping[str, WriteMode | None],
    contracts: frozenset[str],
) -> list[str]:
    ceiling = policy.agents.get(agent.id)
    if ceiling is None:
        return [f"{agent.id}: el agente no tiene entrada en la política"]
    problems = []
    if agent.profile not in catalog.profiles:
        problems.append(f"{agent.id}: perfil '{agent.profile}' no existe en el catálogo")
    problems += [
        f"{agent.id}: contrato '{contract}' no registrado"
        for contract in (agent.contract_in, agent.contract_out)
        if contract not in contracts
    ]
    return problems + (
        _check_reads(agent, ceiling, policy)
        + _check_writes(agent, ceiling, policy)
        + _check_tools(agent, ceiling, tools)
    )


def _duplicates(agents: Iterable[AgentConfig]) -> list[str]:
    seen: set[str] = set()
    problems = []
    for agent in agents:
        if agent.id in seen:
            problems.append(f"{agent.id}: id de agente duplicado")
        seen.add(agent.id)
    return problems


def _effective_plan_writes(agent: AgentConfig, policy: Policy) -> tuple[WriteGrant, ...]:
    if agent.id != policy.plan_owner or PUBLISH_TOOL not in agent.tools:
        return ()
    return _plan_grants(policy.agents[agent.id])


def resolve_effective(
    policy: Policy,
    catalog: ModelCatalog,
    agents: tuple[AgentConfig, ...],
    *,
    tools: Mapping[str, WriteMode | None],
    contracts: frozenset[str],
) -> Mapping[str, EffectivePermissions]:
    """Valida política y agentes juntos; con cualquier problema no se carga nada."""
    problems = validate_policy(policy) + _duplicates(agents)
    for agent in agents:
        problems += _check_agent(agent, policy, catalog, tools, contracts)
    if problems:
        raise ConfigRejectedError(_SOURCE, tuple(problems))
    return MappingProxyType(
        {
            agent.id: EffectivePermissions(
                agent_id=agent.id,
                profile=agent.profile,
                reads=agent.reads,
                writes=agent.writes,
                tools=frozenset(agent.tools),
                forbidden_write=policy.forbidden_write,
                plan_writes=_effective_plan_writes(agent, policy),
            )
            for agent in agents
        }
    )


def protected_roots(policy: Policy) -> tuple[VaultPath, ...]:
    """Carpetas que ningún caso de uso escribe: el mínimo de las reglas más lo que añada la
    política. La política puede ampliar la lista, nunca reducirla."""
    roots = list(DEFAULT_PROTECTED_ROOTS)
    for root in policy.forbidden_write:
        if not within_any(root, roots):
            roots.append(root)
    return tuple(roots)
