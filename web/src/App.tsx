import { useEffect, useState, type KeyboardEvent } from "react";
import { checkSession, onSessionLost, postVoid, resetSession } from "./api/client";
import { ViewBoundary } from "./components/ViewBoundary";
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
  return TABS.find((tab) => tab.id === hash)?.id ?? "hoy";
}

function nextTab(current: TabId, key: string): TabId | null {
  const index = TABS.findIndex((item) => item.id === current);
  const target =
    key === "ArrowRight" ? (index + 1) % TABS.length
    : key === "ArrowLeft" ? (index - 1 + TABS.length) % TABS.length
    : key === "Home" ? 0
    : key === "End" ? TABS.length - 1
    : null;
  return target === null ? null : (TABS[target]?.id ?? null);
}

export function App() {
  const [session, setSession] = useState<SessionState>("checking");
  const [tab, setTab] = useState<TabId>(initialTab);
  // Las pestañas visitadas siguen montadas (ocultas): no se pierden chats ni borradores.
  const [visited, setVisited] = useState<ReadonlySet<TabId>>(() => new Set([initialTab()]));
  const [logoutError, setLogoutError] = useState<string | null>(null);

  useEffect(() => {
    checkSession()
      .then((isOpen) => setSession(isOpen ? "open" : "closed"))
      .catch(() => setSession("error"));
    return onSessionLost(() => {
      resetSession();
      setSession("closed");
    });
  }, []);

  function select(id: TabId) {
    setTab(id);
    setVisited((current) => new Set([...current, id]));
    window.history.replaceState(null, "", `#${id}`);
  }

  useEffect(() => {
    const label = TABS.find((item) => item.id === tab)?.label ?? "";
    window.document.title = `${label} · Life OS`;
  }, [tab]);

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    const next = nextTab(tab, event.key);
    if (next === null) return;
    event.preventDefault();
    select(next);
    document.getElementById(`tab-${next}`)?.focus();
  }

  async function logout() {
    try {
      await postVoid("/api/logout", {});
      resetSession();
      setSession("closed");
    } catch (error) {
      setLogoutError(error instanceof Error ? error.message : String(error));
    }
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
      {logoutError !== null && (
        <p role="alert" className="banner banner-error">
          No se pudo cerrar la sesión: {logoutError}
        </p>
      )}
      <main>
        {TABS.filter((item) => visited.has(item.id)).map(({ id, View }) => (
          <div
            key={id}
            id={`panel-${id}`}
            role="tabpanel"
            aria-labelledby={`tab-${id}`}
            hidden={id !== tab}
          >
            <ViewBoundary>
              <View />
            </ViewBoundary>
          </div>
        ))}
      </main>
    </>
  );
}
