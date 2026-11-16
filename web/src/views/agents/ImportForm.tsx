import { useState, type ChangeEvent, type FormEvent } from "react";
import { ApiError, post } from "../../api/client";
import type { ImportedAgent } from "../../api/types";

// Mismo límite que la API (web/schemas.py): un agente real ocupa ~1 KiB.
export const MAX_AGENT_FILE = 32 * 1024;

function violations(error: unknown): string[] {
  if (!(error instanceof ApiError) || typeof error.data !== "object" || error.data === null) return [];
  const list = (error.data as { violations?: unknown }).violations;
  return Array.isArray(list) ? list.filter((item): item is string => typeof item === "string") : [];
}

// Importar o editar: pegar el YAML o elegir un archivo. Importar nunca activa (RULES E.10).
type Props = { onImported: () => void };

export function ImportForm({ onImported }: Props) {
  const [name, setName] = useState("agente.yaml");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [done, setDone] = useState<string | null>(null);

  async function choose(event: ChangeEvent<HTMLInputElement>) {
    const input = event.target;
    const file = input.files?.[0];
    if (file === undefined) return;
    setDone(null);
    setProblems([]);
    if (file.size > MAX_AGENT_FILE) {
      setError(`El archivo supera ${MAX_AGENT_FILE / 1024} KiB.`);
      input.value = "";
      return;
    }
    try {
      setText(await file.text());
      setName(file.name.replace(/[^A-Za-z0-9._-]/g, "_") || "agente.yaml");
      setError(null);
    } catch {
      setError("No se pudo leer el archivo.");
      input.value = "";
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setProblems([]);
    setDone(null);
    try {
      const agent = await post<ImportedAgent>("/api/agentes/importacion", { nombre: name, contenido: text });
      setDone(`${agent.name}: v${agent.current} en ${agent.state}. Pruébalo antes de aprobarlo.`);
      onImported();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setProblems(violations(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="card" onSubmit={submit} aria-labelledby="import-heading">
      <h2 id="import-heading">Importar o editar un agente</h2>
      <p className="muted small">
        Pega el YAML (o elige un archivo). Para editar, exporta, cambia y sube <code>version</code>. Se
        registra en borrador: no se activa ni recibe más permisos que tu política.
      </p>
      <label>
        Archivo
        <input type="file" accept=".yaml,.yml,text/yaml" onChange={(event) => void choose(event)} />
      </label>
      <label>
        Configuración YAML
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          rows={10}
          maxLength={MAX_AGENT_FILE}
          spellCheck={false}
          required
        />
      </label>
      <button type="submit" disabled={busy || text.trim() === ""}>
        {busy ? "Importando…" : "Importar en borrador"}
      </button>
      {done !== null && (
        <p role="status" className="banner banner-ok">
          {done}
        </p>
      )}
      {error !== null && (
        <div role="alert" className="banner banner-error">
          <p>{error}</p>
          {problems.length > 0 && (
            <ul>
              {problems.map((problem, index) => (
                <li key={`${index}-${problem}`}>{problem}</li>
              ))}
            </ul>
          )}
        </div>
      )}
    </form>
  );
}
