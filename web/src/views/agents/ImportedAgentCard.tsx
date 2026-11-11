import { useState } from "react";
import { get, post } from "../../api/client";
import type { AgentState, Exported, Grant, ImportedAgent, RunResult } from "../../api/types";
import { ProposalItemText } from "./ProposalsPanel";

type Action = "aprobar" | "activar" | "desactivar";

// Solo orienta qué botón tiene sentido; el backend decide y rechaza lo que no proceda.
const ALLOWED: Record<AgentState, Action[]> = {
  borrador: ["desactivar"],
  probado: ["aprobar", "desactivar"],
  aprobado: ["activar", "desactivar"],
  activo: ["desactivar"],
  desactivado: ["activar"],
};

const LABEL: Record<Action, string> = { aprobar: "Aprobar", activar: "Activar", desactivar: "Desactivar" };

function grant(item: Grant): string {
  return `${item.root} (${item.mode})`;
}

function RunSummary({ result }: { result: RunResult }) {
  return (
    <div className="preview" aria-live="polite">
      <p>
        {result.dry_run ? "Prueba sin escrituras" : "Ejecución"} · {result.week} · fuente {result.source}
        {result.path !== null && ` · escrito en ${result.path}`}
      </p>
      {result.items.length === 0 ? (
        <p className="muted">Nada que proponer.</p>
      ) : (
        <ul>
          {result.items.map((item) => (
            <li key={item.key}>
              <ProposalItemText item={item} />
            </li>
          ))}
        </ul>
      )}
      {result.alerts.map((alert) => (
        <p key={alert} className="banner banner-warn">
          {alert}
        </p>
      ))}
    </div>
  );
}

export function ImportedAgentCard({ agent, onChanged }: { agent: ImportedAgent; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [exported, setExported] = useState<Exported | null>(null);
  const [restoreTo, setRestoreTo] = useState<number>(agent.versions[0]?.number ?? 1);

  async function act(run: () => Promise<unknown>) {
    setBusy(true);
    setError(null);
    try {
      await run();
      onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const base = `/api/agentes/${encodeURIComponent(agent.id)}`;
  const others = agent.versions.filter((version) => version.number !== agent.current);
  return (
    <article className="card" aria-labelledby={`imported-${agent.id}`}>
      <h3 id={`imported-${agent.id}`}>
        {agent.name} <span className="tag">{agent.state}</span> <span className="muted">v{agent.current}</span>
      </h3>
      <p>{agent.purpose}</p>
      <dl className="facts">
        <dt>Área</dt>
        <dd>{agent.area ?? "—"}</dd>
        {agent.effective !== null && (
          <>
            <dt>Lectura</dt>
            <dd>{agent.effective.reads.join(", ") || "—"}</dd>
            <dt>Escritura</dt>
            <dd>{agent.effective.writes.map(grant).join(", ") || "—"}</dd>
            <dt>Herramientas</dt>
            <dd>{agent.effective.tools.join(", ")}</dd>
          </>
        )}
        <dt>Versiones</dt>
        <dd>
          {agent.versions.map((version) => `v${version.number} (${version.sha256})`).join(", ")}
        </dd>
      </dl>
      {agent.problems.length > 0 && (
        <div role="alert" className="banner banner-warn">
          <p>Hoy la política no permite ejecutarlo:</p>
          <ul>
            {agent.problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="row">
        <button
          type="button"
          disabled={busy || agent.state === "desactivado"}
          onClick={() =>
            void act(async () => setResult(await post<RunResult>(`${base}/prueba`, {})))
          }
        >
          Probar
        </button>
        {ALLOWED[agent.state].map((action) => (
          <button
            key={action}
            type="button"
            className={action === "desactivar" ? "ghost" : undefined}
            disabled={busy}
            onClick={() => void act(() => post(`${base}/estado`, { accion: action }))}
          >
            {LABEL[action]}
          </button>
        ))}
        <button
          type="button"
          disabled={busy || agent.state !== "activo"}
          onClick={() =>
            void act(async () => setResult(await post<RunResult>(`${base}/ejecucion`, {})))
          }
        >
          Ejecutar
        </button>
        <button
          type="button"
          className="ghost"
          disabled={busy}
          onClick={() => void act(async () => setExported(await get<Exported>(`${base}/exportacion`)))}
        >
          Exportar
        </button>
      </div>
      {others.length > 0 && (
        <div className="row">
          <label>
            Restaurar versión
            <select value={restoreTo} onChange={(event) => setRestoreTo(Number(event.target.value))}>
              {others.map((version) => (
                <option key={version.number} value={version.number}>
                  v{version.number}
                </option>
              ))}
            </select>
          </label>
          <button
            type="button"
            disabled={busy || !others.some((version) => version.number === restoreTo)}
            onClick={() => void act(() => post(`${base}/estado`, { accion: "restaurar", version: restoreTo }))}
          >
            Restaurar
          </button>
        </div>
      )}
      {error !== null && (
        <p role="alert" className="banner banner-error">
          {error}
        </p>
      )}
      {result !== null && <RunSummary result={result} />}
      {exported !== null && (
        <details open>
          <summary>Archivo de la v{exported.version}</summary>
          <pre className="preview">{exported.contenido}</pre>
        </details>
      )}
    </article>
  );
}
