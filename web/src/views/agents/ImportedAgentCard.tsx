import { useId, useState } from "react";
import { get, post } from "../../api/client";
import type { AgentState, AgentVersion, Exported, Grant, ImportedAgent, RunResult } from "../../api/types";
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
    <div className="preview">
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
      {result.alerts.map((alert, index) => (
        <p key={`${index}-${alert}`} className="banner banner-warn">
          {alert}
        </p>
      ))}
    </div>
  );
}

type RestoreProps = {
  others: AgentVersion[];
  isBusy: boolean;
  name: string;
  onRestore: (version: number) => void;
};

function RestoreControl({ others, isBusy, name, onRestore }: RestoreProps) {
  const [chosen, setChosen] = useState<number | null>(null);
  // Se deriva en cada render: tras restaurar o importar, la lista cambia y la elección se ajusta.
  const selected = others.some((version) => version.number === chosen) ? chosen : (others[0]?.number ?? null);
  if (selected === null) return null;
  return (
    <div className="row">
      <label>
        Restaurar versión de {name}
        <select value={selected} onChange={(event) => setChosen(Number(event.target.value))}>
          {others.map((version) => (
            <option key={version.number} value={version.number}>
              v{version.number}
            </option>
          ))}
        </select>
      </label>
      <button type="button" disabled={isBusy} onClick={() => onRestore(selected)}>
        Restaurar
      </button>
    </div>
  );
}

type Props = { agent: ImportedAgent; onChanged: () => void };

export function ImportedAgentCard({ agent, onChanged }: Props) {
  const headingId = useId();
  const [isBusy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<RunResult | null>(null);
  const [exported, setExported] = useState<Exported | null>(null);

  async function act(run: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await run();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }

  const base = `/api/agentes/${encodeURIComponent(agent.id)}`;
  const changeState = (body: { accion: string; version?: number }) =>
    void act(async () => {
      await post(`${base}/estado`, body);
      // La versión o el estado cambiaron: la prueba y la exportación anteriores ya no valen.
      setResult(null);
      setExported(null);
      onChanged();
    });
  const runAgent = (path: "prueba" | "ejecucion") =>
    void act(async () => {
      setResult(await post<RunResult>(`${base}/${path}`, {}));
      onChanged();
    });
  const actions = ALLOWED[agent.state] ?? [];
  const others = agent.versions.filter((version) => version.number !== agent.current);
  const shownExport = exported !== null && exported.version === agent.current ? exported : null;

  return (
    <article className="card" aria-labelledby={headingId}>
      <h4 id={headingId}>
        {agent.name} <span className="tag">{agent.state}</span> <span className="muted">v{agent.current}</span>
      </h4>
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
        <dd>{agent.versions.map((version) => `v${version.number} (${version.sha256})`).join(", ")}</dd>
      </dl>
      {agent.problems.length > 0 && (
        <div className="banner banner-warn">
          <p>Hoy la política no permite ejecutarlo:</p>
          <ul>
            {agent.problems.map((problem, index) => (
              <li key={`${index}-${problem}`}>{problem}</li>
            ))}
          </ul>
        </div>
      )}
      <div className="row" aria-label={`Acciones de ${agent.name}`} role="group">
        <button type="button" disabled={isBusy || agent.state === "desactivado"} onClick={() => runAgent("prueba")}>
          Probar
        </button>
        {actions.map((action) => (
          <button
            key={action}
            type="button"
            className={action === "desactivar" ? "ghost" : undefined}
            disabled={isBusy}
            onClick={() => changeState({ accion: action })}
          >
            {LABEL[action]}
          </button>
        ))}
        <button type="button" disabled={isBusy || agent.state !== "activo"} onClick={() => runAgent("ejecucion")}>
          Ejecutar
        </button>
        <button
          type="button"
          className="ghost"
          disabled={isBusy}
          onClick={() => void act(async () => setExported(await get<Exported>(`${base}/exportacion`)))}
        >
          Exportar
        </button>
      </div>
      <RestoreControl
        others={others}
        isBusy={isBusy}
        name={agent.name}
        onRestore={(version) => changeState({ accion: "restaurar", version })}
      />
      <div aria-live="polite">
        {error !== null && <p className="banner banner-error">{error}</p>}
        {result !== null && <RunSummary result={result} />}
      </div>
      {shownExport !== null && (
        <details open>
          <summary>Archivo de la v{shownExport.version}</summary>
          <pre className="preview">{shownExport.contenido}</pre>
        </details>
      )}
    </article>
  );
}
