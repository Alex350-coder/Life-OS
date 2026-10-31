import { useEffect, useRef, useState } from "react";
import { post } from "../api/client";
import type { Briefing, ClosingAnswer, ClosingPreview, ClosingResult, TaskChange } from "../api/types";
import { ErrorBanner } from "../components/common";

type Kind = "" | ClosingAnswer["tipo"];

interface Draft {
  kind: Kind;
  progress: string;
  remaining: string;
}

const EMPTY: Draft = { kind: "", progress: "", remaining: "" };

const LABELS: Record<Kind, string> = { "": "Sin respuesta", hecha: "Hecha", parcial: "Parcial", no: "No la hice" };

// Solo las tareas que siguen en el plan de hoy: un borrador de una tarea que ya no se ve no se envía.
function answersFrom(drafts: Record<string, Draft>, visible: ReadonlySet<string>): ClosingAnswer[] {
  const answers: ClosingAnswer[] = [];
  for (const [tarea, draft] of Object.entries(drafts)) {
    if (draft.kind === "" || !visible.has(tarea)) continue; // sin respuesta: la tarea no cambia
    const answer: ClosingAnswer = { tarea, tipo: draft.kind };
    if (draft.kind === "parcial") {
      if (draft.progress.trim() !== "") answer.avance = draft.progress.trim();
      if (draft.remaining.trim() !== "") answer.restante = draft.remaining.trim();
    }
    answers.push(answer);
  }
  return answers;
}

function describe(change: TaskChange): string {
  if (change.kind === "hecha") return `${change.title} → completada`;
  if (change.kind === "no") return `${change.title} → no realizada: propuesta de reubicación`;
  const parts: string[] = [];
  if (change.progress_after !== null) parts.push(`avance ${change.progress_before ?? "?"} → ${change.progress_after}`);
  if (change.remaining_after !== null) parts.push(`restante ${change.remaining_before ?? "?"} → ${change.remaining_after}`);
  return `${change.title} → ${parts.join(", ")}`;
}

type Props = {
  day: Briefing;
  onApplied: () => void;
};

export function ClosingForm({ day, onApplied }: Props) {
  const tasks = [...new Map(day.work.filter((b) => b.task_id !== null).map((b) => [b.task_id ?? "", b.title])).entries()];
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [preview, setPreview] = useState<ClosingPreview | null>(null);
  const [applied, setApplied] = useState<ClosingResult | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isBusy, setIsBusy] = useState(false);
  const resultRef = useRef<HTMLParagraphElement>(null);
  const visible = new Set(tasks.map(([task]) => task));

  useEffect(() => {
    if (applied !== null) resultRef.current?.focus();
  }, [applied]);

  const update = (task: string, patch: Partial<Draft>) => {
    setPreview(null);
    setDrafts((current) => ({ ...current, [task]: { ...(current[task] ?? EMPTY), ...patch } }));
  };

  async function run(path: string, onDone: (result: ClosingResult) => void) {
    setIsBusy(true);
    setError(null);
    try {
      onDone(await post<ClosingResult>(path, { respuestas: answersFrom(drafts, visible) }));
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    } finally {
      setIsBusy(false);
    }
  }

  if (tasks.length === 0) return null;
  return (
    <section aria-labelledby="closing-heading" className="card">
      <h2 id="closing-heading">Cierre del día</h2>
      <p className="muted">
        Solo cambia lo que respondas. Primero revisa la vista previa; nada se aplica sin confirmar.
      </p>
      <ErrorBanner error={error} />
      {applied !== null ? (
        <p role="status" className="banner banner-ok" tabIndex={-1} ref={resultRef}>
          Cierre registrado en {applied.written.join(", ")}.
          {applied.adjustments.length > 0 && ` Propuestas de reubicación para el Jefe: ${applied.adjustments.length}.`}
        </p>
      ) : (
        <>
          {tasks.map(([task, title]) => {
            const draft = drafts[task] ?? EMPTY;
            return (
              <fieldset key={task} className="closing-task">
                <legend>
                  {title} <code className="task-id">{task}</code>
                </legend>
                {(["", "hecha", "parcial", "no"] as const).map((kind) => (
                  <label key={kind} className="check">
                    <input
                      type="radio"
                      name={`cierre-${task}`}
                      value={kind}
                      checked={draft.kind === kind}
                      onChange={() => update(task, { kind })}
                    />
                    {LABELS[kind]}
                  </label>
                ))}
                {draft.kind === "parcial" && (
                  <div className="row">
                    <label>
                      Avance (%)
                      <input inputMode="numeric" value={draft.progress} maxLength={4} onChange={(e) => update(task, { progress: e.target.value })} />
                    </label>
                    <label>
                      Restante (p. ej., 1h30m)
                      <input value={draft.remaining} maxLength={10} onChange={(e) => update(task, { remaining: e.target.value })} />
                    </label>
                  </div>
                )}
              </fieldset>
            );
          })}
          <div className="row">
            <button type="button" disabled={isBusy} onClick={() => void run("/api/cierre/vista-previa", setPreview)}>
              Ver vista previa
            </button>
            <button
              type="button"
              disabled={isBusy || preview === null}
              onClick={() =>
                void run("/api/cierre", (result) => {
                  setApplied(result);
                  onApplied();
                })
              }
            >
              Confirmar cierre
            </button>
          </div>
          {preview !== null && (
            <div role="status" className="preview">
              <h3>Vista previa del cierre de {preview.day} (no se cambió nada)</h3>
              {preview.changes.length === 0 ? <p>Sin respuestas.</p> : <ul>{preview.changes.map((c) => <li key={c.task_id}>{describe(c)}</li>)}</ul>}
              {preview.unanswered.length > 0 && <p className="muted">Sin respuesta (no cambian): {preview.unanswered.join(", ")}</p>}
            </div>
          )}
        </>
      )}
    </section>
  );
}
