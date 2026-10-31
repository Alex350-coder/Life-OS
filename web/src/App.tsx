import { useEffect, useState, type KeyboardEvent } from "react";
import { checkSession, post, resetSession } from "./api/client";
import { AgentsView } from "./views/AgentsView";
import { HistoryView } from "./views/HistoryView";
import { TasksView } from "./views/TasksView";
import { TodayView } from "./views/TodayView";
import { WeekView } from "./views/WeekView";

const TABS = [
  { id: "hoy", label: "Hoy", View: TodayView },
  { id: "semana", label: "Semana", View: WeekView },
  { id: "tareas", label: "Tareas", View: TasksView },
  { id: "historial", label: "Historial", View: HistoryView },
  { id: "agentes", label: "Agentes", View: AgentsView },
] as const;

type TabId = (typeof TABS)[number]["id"];
type SessionState = "checking" | "open" | "closed" | "error";

function initialTab(): TabId {
  const hash = window.location.hash.replace("#", "");
  return TABS.some((tab) => tab.id === hash) ? (hash as TabId) : "hoy";
}

export function App() {
  const [session, setSession] = useState<SessionState>("checking");
  const [tab, setTab] = useState<TabId>(initialTab);

  useEffect(() => {
    checkSession()
      .then((isOpen) => setSession(isOpen ? "open" : "closed"))
      .catch(() => setSession("error"));
  }, []);

  function select(id: TabId) {
    setTab(id);
    window.history.replaceState(null, "", `#${id}`);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const index = TABS.findIndex((item) => item.id === tab);
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (step === 0) return;
    const next = TABS[(index + step + TABS.length) % TABS.length];
    if (next === undefined) return;
    select(next.id);
    document.getElementById(`tab-${next.id}`)?.focus();
  }

  async function logout() {
    await post<undefined>("/api/logout", {}).catch(() => undefined);
    resetSession();
    setSession("closed");
  }

  if (session === "checking") return <p className="center">Comprobando la sesión…</p>;
  if (session !== "open") {
    return (
      <main className="center">
        <h1>Life OS</h1>
        <p role="alert">
          {session === "closed"
            ? "No hay sesión. Abre la URL de un solo uso que muestra lifeos-web en la terminal."
            : "No se pudo contactar con el servidor local. ¿Está en marcha lifeos-web?"}
        </p>
      </main>
    );
  }

  const active = TABS.find((item) => item.id === tab) ?? TABS[0];
  return (
    <>
      <header className="top">
        <h1>Life OS</h1>
        <nav aria-label="Secciones">
          <div role="tablist" aria-label="Vistas">
            {TABS.map((item) => (
              <button
                key={item.id}
                id={`tab-${item.id}`}
                role="tab"
                type="button"
                aria-selected={item.id === tab}
                aria-controls={`panel-${item.id}`}
                tabIndex={item.id === tab ? 0 : -1}
                onClick={() => select(item.id)}
                onKeyDown={handleKeyDown}
              >
                {item.label}
              </button>
            ))}
          </div>
        </nav>
        <button type="button" className="link" onClick={() => void logout()}>
          Cerrar sesión
        </button>
      </header>
      <main id={`panel-${active.id}`} role="tabpanel" aria-labelledby={`tab-${active.id}`}>
        <active.View />
      </main>
    </>
  );
}
