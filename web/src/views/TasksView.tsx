import { useState, type FormEvent } from "react";
import { get, post } from "../api/client";
import type { Task } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorBanner, Loading } from "../components/common";

// Mismos valores que domain/task.py (Area y TaskStatus).
const AREAS = ["", "universidad", "proyectos", "carrera", "hobbies", "salud", "finanzas", "cotidiano"];
const STATUSES = ["", "propuesta", "programada", "bloqueada", "confirmada", "completada", "cancelada"];

function TaskTable({ tasks, caption }: { tasks: Task[]; caption: string }) {
  if (tasks.length === 0) return <p className="muted">Sin tareas.</p>;
  return (
    <div className="table-wrap">
      <table>
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Tarea</th>
            <th scope="col">Estado</th>
            <th scope="col">Área</th>
            <th scope="col">Esfuerzo</th>
            <th scope="col">Vence</th>
            <th scope="col">Nota</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map((task) => (
            <tr key={`${task.path}-${task.id ?? task.title}`}>
              <td>
                {task.title} {task.id !== null && <code className="task-id">{task.id}</code>}
              </td>
              <td>{task.status}</td>
              <td>{task.area ?? "?"}</td>
              <td>{task.effort ?? "?"}{task.progress !== null && ` · ${task.progress}`}</td>
              <td>{task.due ?? "—"}</td>
              <td className="muted small">{task.path}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Capture({ onSaved }: { onSaved: () => void }) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<Error | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    try {
      const result = await post<{ path: string }>("/api/capturas", { texto: text });
      setSaved(result.path);
      setText("");
      onSaved();
    } catch (reason) {
      setError(reason instanceof Error ? reason : new Error(String(reason)));
    }
  }

  return (
    <section aria-labelledby="capture-heading" className="card">
      <h2 id="capture-heading">Captura rápida</h2>
      <form className="row" onSubmit={handleSubmit}>
        <label className="wide">
          Idea, tarea o recordatorio (va a 00-Inbox/)
          <input value={text} required maxLength={2000} onChange={(e) => setText(e.target.value)} />
        </label>
        <button type="submit" disabled={text.trim() === ""}>
          Guardar
        </button>
      </form>
      <ErrorBanner error={error} />
      {saved !== null && <p role="status">Captura guardada en {saved}</p>}
    </section>
  );
}

export function TasksView() {
  const [area, setArea] = useState("");
  const [status, setStatus] = useState("");
  const [text, setText] = useState("");
  const [version, setVersion] = useState(0);
  const upcoming = useApi(() => get<Task[]>("/api/tareas/proximas", { dias: 14 }), String(version));
  const all = useApi(
    () => get<Task[]>("/api/tareas", { area, estado: status, texto: text }),
    `${area}|${status}|${text}|${version}`,
  );

  return (
    <div className="view">
      <Capture onSaved={() => setVersion((n) => n + 1)} />
      <section aria-labelledby="upcoming-heading" className="card">
        <h2 id="upcoming-heading">Próximos vencimientos (14 días y atrasadas)</h2>
        <ErrorBanner error={upcoming.error} />
        <Loading loading={upcoming.loading && upcoming.data === null}>
          <TaskTable tasks={upcoming.data ?? []} caption="Tareas abiertas por fecha de vencimiento" />
        </Loading>
      </section>
      <section aria-labelledby="tasks-heading" className="card">
        <h2 id="tasks-heading">Todas las tareas</h2>
        <div className="row">
          <label>
            Área
            <select value={area} onChange={(e) => setArea(e.target.value)}>
              {AREAS.map((value) => <option key={value} value={value}>{value || "(todas)"}</option>)}
            </select>
          </label>
          <label>
            Estado
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              {STATUSES.map((value) => <option key={value} value={value}>{value || "(todos)"}</option>)}
            </select>
          </label>
          <label>
            Buscar en el título
            <input value={text} maxLength={200} onChange={(e) => setText(e.target.value)} />
          </label>
        </div>
        <ErrorBanner error={all.error} />
        <Loading loading={all.loading && all.data === null}>
          <TaskTable tasks={all.data ?? []} caption="Tareas de la bóveda (sin archivadas)" />
        </Loading>
      </section>
    </div>
  );
}
