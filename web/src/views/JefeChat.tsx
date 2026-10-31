import { useState } from "react";
import { ApiError, post } from "../api/client";
import type { PlanResult } from "../api/types";
import { ChatLog, type ChatMessage } from "../components/ChatLog";
import { jefeReply } from "../components/chatFormat";

type Action = "propuesta" | "aprobacion" | "revision";

const ACTIONS: { action: Action; label: string; usesAi: boolean }[] = [
  { action: "propuesta", label: "Proponer la semana", usesAi: true },
  { action: "aprobacion", label: "Aprobar el plan propuesto", usesAi: false },
  { action: "revision", label: "Replanificar desde ahora", usesAi: true },
];

// Un plan rechazado llega como 422 con el resultado (conflictos) en `data`.
function rejected(error: unknown): PlanResult | null {
  if (error instanceof ApiError && error.code === "plan_rechazado" && error.data !== null) {
    return error.data as PlanResult;
  }
  return null;
}

type Props = {
  week: string;
  onChanged: () => void;
};

export function JefeChat({ week, onChanged }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [useAi, setUseAi] = useState(false);
  const [isBusy, setIsBusy] = useState(false);

  const say = (author: ChatMessage["author"], lines: string[], isError = false) =>
    setMessages((current) => [...current, { id: current.length + 1, author, lines, isError }]);

  async function run(action: Action, label: string, usesAi: boolean) {
    const ai = usesAi && useAi;
    say("Tú", [`${label}${week === "" ? "" : ` (${week})`}${ai ? " con IA" : ""}`]);
    setIsBusy(true);
    try {
      const body = action === "aprobacion" ? { semana: week || null } : { semana: week || null, ia: ai };
      say("Jefe", jefeReply(await post<PlanResult>(`/api/plan/${action}`, body)));
      onChanged();
    } catch (error) {
      const plan = rejected(error);
      if (plan !== null) say("Jefe", jefeReply(plan), true);
      else say("Jefe", [error instanceof Error ? error.message : String(error)], true);
    } finally {
      setIsBusy(false);
    }
  }

  return (
    <section aria-labelledby="jefe-heading" className="card">
      <h2 id="jefe-heading">Chat con el Jefe</h2>
      <p className="muted">
        El Jefe propone y valida; nada queda aprobado hasta que tú lo apruebas.
      </p>
      <ChatLog label="Conversación con el Jefe" messages={messages} />
      <div className="row">
        {ACTIONS.map(({ action, label, usesAi }) => (
          <button key={action} type="button" disabled={isBusy} onClick={() => void run(action, label, usesAi)}>
            {label}
          </button>
        ))}
        <label className="check">
          <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
          Proponer con IA
        </label>
      </div>
    </section>
  );
}
