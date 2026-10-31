import { useState, type FormEvent } from "react";
import { post } from "../api/client";
import type { CheckInResult, PlanBlock } from "../api/types";
import { ChatLog, type ChatMessage } from "../components/ChatLog";
import { coachReply } from "../components/chatFormat";

// Estados del check-in (domain/coach.py CheckInState). La respuesta la decide el Coach.
const STATES = [
  ["nota", "Solo una nota"],
  ["empezando", "Empiezo"],
  ["hecho", "Terminé el bloque"],
  ["bloqueado", "Estoy bloqueado"],
  ["cansado", "Estoy cansado"],
  ["perdido", "Perdí el bloque"],
] as const;

type Props = {
  work: PlanBlock[];
  onRecorded: () => void;
};

export function CoachChat({ work, onRecorded }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [text, setText] = useState("");
  const [state, setState] = useState<(typeof STATES)[number][0]>("nota");
  const [task, setTask] = useState("");
  const [capture, setCapture] = useState("");
  const [useAi, setUseAi] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const tasks = [...new Map(work.filter((b) => b.task_id !== null).map((b) => [b.task_id, b])).values()];
  // Si la tarea elegida ya no está en el plan (tras recargar), no se envía una tarea oculta.
  const chosenTask = tasks.some((b) => b.task_id === task) ? task : "";

  const say = (author: ChatMessage["author"], lines: string[], isError = false) =>
    setMessages((current) => [...current, { id: current.length + 1, author, lines, isError }]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const label = STATES.find(([value]) => value === state)?.[1] ?? state;
    say("Tú", [text.trim() === "" ? label : `${label}: ${text.trim()}`]);
    setIsSending(true);
    try {
      const result = await post<CheckInResult>("/api/checkins", {
        texto: text,
        estado: state,
        tarea: chosenTask === "" ? null : chosenTask,
        capturar: capture.trim() === "" ? null : capture,
        ia: useAi,
      });
      say("Coach", coachReply(result));
      setText("");
      setCapture("");
      onRecorded();
    } catch (error) {
      say("Coach", [error instanceof Error ? error.message : String(error)], true);
    } finally {
      setIsSending(false);
    }
  }

  return (
    <section aria-labelledby="coach-heading" className="card">
      <h2 id="coach-heading">Chat con el Coach</h2>
      <ChatLog label="Conversación con el Coach" messages={messages} />
      <form onSubmit={handleSubmit}>
        <fieldset className="chat-form plain" disabled={isSending}>
          <legend className="visually-hidden">Nuevo check-in</legend>
        <label>
          ¿Cómo vas?
          <select value={state} onChange={(e) => setState(e.target.value as (typeof STATES)[number][0])}>
            {STATES.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Tarea del bloque
          <select value={chosenTask} onChange={(e) => setTask(e.target.value)}>
            <option value="">(ninguna)</option>
            {tasks.map((block) => (
              <option key={block.task_id} value={block.task_id ?? ""}>
                {block.title} ({block.task_id})
              </option>
            ))}
          </select>
        </label>
        <label className="wide">
          Mensaje
          <textarea value={text} maxLength={2000} rows={2} onChange={(e) => setText(e.target.value)} />
        </label>
        <label className="wide">
          Captura rápida (opcional, va a 00-Inbox/)
          <input value={capture} maxLength={2000} onChange={(e) => setCapture(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={useAi} onChange={(e) => setUseAi(e.target.checked)} />
          Pedir el mensaje al modelo (IA)
        </label>
        <button type="submit">Enviar check-in</button>
        </fieldset>
        <p role="status" className="muted small">{isSending ? "Enviando…" : ""}</p>
      </form>
    </section>
  );
}
