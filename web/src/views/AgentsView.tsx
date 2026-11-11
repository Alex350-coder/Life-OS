import { useState } from "react";
import { get } from "../api/client";
import type { Agent, Agents, Grant } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorBanner, Loading } from "../components/common";
import { ImportedAgentCard } from "./agents/ImportedAgentCard";
import { ImportForm } from "./agents/ImportForm";
import { ProposalsPanel } from "./agents/ProposalsPanel";

function grant(item: Grant): string {
  return `${item.root} (${item.mode}${item.section !== null ? `: ${item.section}` : ""})`;
}

function AgentCard({ agent }: { agent: Agent }) {
  const usage = agent.usage;
  return (
    <article className="card" aria-labelledby={`agent-${agent.id}`}>
      <h3 id={`agent-${agent.id}`}>
        {agent.name} <span className="tag">{agent.status}</span> <span className="muted">v{agent.version}</span>
      </h3>
      <p>{agent.purpose}</p>
      <dl className="facts">
        <dt>Modelo</dt>
        <dd>
          {agent.model} <span className="muted">({agent.provider}, perfil {agent.profile})</span>
        </dd>
        <dt>Contratos</dt>
        <dd>
          {agent.contract_in} → {agent.contract_out}
        </dd>
        <dt>Herramientas</dt>
        <dd>{agent.tools.join(", ")}</dd>
        <dt>Lectura</dt>
        <dd>{agent.reads.join(", ") || "—"}</dd>
        <dt>Escritura</dt>
        <dd>{agent.writes.map(grant).join(", ") || "—"}</dd>
        {agent.plan_writes.length > 0 && (
          <>
            <dt>Publica el plan</dt>
            <dd>{agent.plan_writes.map(grant).join(", ")}</dd>
          </>
        )}
        <dt>Uso del mes</dt>
        <dd>
          {usage === null
            ? "Sin llamadas al modelo."
            : `${usage.calls} llamadas, ${usage.attempts} intentos, ${usage.errors} errores, ` +
              `tokens ${usage.tokens_prompt}/${usage.tokens_completion}, costo ${usage.cost_estimated_usd} USD` +
              (usage.cost_unknown > 0 ? ` + ${usage.cost_unknown} desconocido(s)` : "")}
        </dd>
      </dl>
    </article>
  );
}

export function AgentsView() {
  const agents = useApi(() => get<Agents>("/api/agentes"), "agentes");
  // Tras aceptar, importar o ejecutar se recargan los agentes y las propuestas.
  const [changes, setChanges] = useState(0);
  const data = agents.data;
  const imported = data?.imported ?? [];

  function changed() {
    setChanges((n) => n + 1);
    agents.reload();
  }

  return (
    <div className="view">
      <section aria-labelledby="agents-heading" className="card">
        <h2 id="agents-heading">Agentes</h2>
        <p className="muted">
          Permisos efectivos = lo que pide cada agente ∩ tu política. Jefe y Coach son el núcleo; los
          especialistas se importan, se prueban, se aprueban y se activan aquí.
        </p>
        <ErrorBanner error={agents.error} />
        <Loading loading={agents.loading && data === null}>
          {data !== null && (
            <>
              <p>
                Dueño del plan global: <strong>{data.plan_owner}</strong> · Mes: {data.month}
              </p>
              <p className="muted small">
                Ningún agente escribe en: {data.forbidden_write.join(", ")} · Sin lectura de agentes:{" "}
                {data.restricted_read.join(", ")}
              </p>
              {data.agents.map((agent) => (
                <AgentCard key={agent.id} agent={agent} />
              ))}
              <h2>Especialistas importados</h2>
              {imported.length === 0 && <p className="muted">Aún no has importado ningún agente.</p>}
              {imported.map((agent) => (
                <ImportedAgentCard key={agent.id} agent={agent} onChanged={changed} />
              ))}
            </>
          )}
        </Loading>
      </section>
      <ProposalsPanel version={changes} />
      <ImportForm onImported={changed} />
    </div>
  );
}
