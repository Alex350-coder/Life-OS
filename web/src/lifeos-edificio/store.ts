// LifeosStore (F11-T05, ADR-0022): la fuente de datos del mundo y la interfaz de Habblaud en LifeOS. Sustituye
// al store SSE del servidor de Habblaud: consulta las rutas EXISTENTES de la API (con la sesión y el CSRF del
// cliente de siempre), las traduce con el adaptador puro y avisa de lo nuevo del historial. No guarda nada propio
// ni rellena con datos inventados: si una ruta falla, la conexión pasa a «cerrada» y se muestra el error.
import { ApiError, get } from "../api/client";
import type { Agents, Briefing, HistoryEntry, ReviewItem, Task } from "../api/types";
import type { AgentInfo, FeedItem, Notice, OfficeSnapshot, RoomInfo } from "../habblaud/shared/types";
import type { ConnectionState, SnapshotSource, SourceEvents } from "../habblaud/world/source";
import { toFeed, toSnapshot, type LifeosData } from "./adapter";

export const REFRESH_MS = 60_000;
const HISTORY_LIMIT = 200;
const UPCOMING_DAYS = 14;

type Loaded = Omit<LifeosData, "now">;
type Listener<K extends keyof SourceEvents> = (value: SourceEvents[K]) => void;

export interface StoreOptions {
  load?: () => Promise<Loaded>;
  now?: () => number;
}

const NO_PLAN_STATUS = 409;

/** GET /api/hoy responde 409 mientras no hay plan aprobado para hoy: es un estado real, no un fallo. */
export async function todayOrNull(load: () => Promise<Briefing>): Promise<Briefing | null> {
  try {
    return await load();
  } catch (err) {
    if (err instanceof ApiError && err.status === NO_PLAN_STATUS) return null;
    throw err;
  }
}

/** Las cinco rutas existentes que alimentan el edificio, en paralelo. */
export async function loadFromApi(): Promise<Loaded> {
  const [today, agents, proposals, upcoming, history] = await Promise.all([
    todayOrNull(() => get<Briefing>("/api/hoy")),
    get<Agents>("/api/agentes"),
    get<{ items: ReviewItem[] }>("/api/propuestas"),
    get<Task[]>("/api/tareas/proximas", { dias: UPCOMING_DAYS }),
    get<{ entries: HistoryEntry[] }>("/api/historial", { limite: HISTORY_LIMIT }),
  ]);
  return { today, agents, proposals: proposals.items, upcoming, history: history.entries };
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : "error desconocido";
}

export class LifeosStore implements SnapshotSource {
  snapshot: OfficeSnapshot | null = null;
  feed: FeedItem[] = [];
  connection: ConnectionState = "connecting";
  readonly mock = false;
  readonly replaying = false;
  nextRetryAt: number | null = null;
  lastError: string | null = null;
  private rev = 0;
  private seenFeed: Set<string> | null = null;
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly listeners: { [K in keyof SourceEvents]: Set<Listener<K>> } = {
    snapshot: new Set(),
    feed: new Set(),
    notice: new Set(),
    connection: new Set(),
  };
  private readonly load: () => Promise<Loaded>;
  private readonly now: () => number;

  constructor(options: StoreOptions = {}) {
    this.load = options.load ?? loadFromApi;
    this.now = options.now ?? Date.now;
  }

  on<K extends keyof SourceEvents>(event: K, cb: Listener<K>): () => void {
    const set = this.listeners[event] as Set<Listener<K>>;
    set.add(cb);
    return () => set.delete(cb);
  }

  agent(id: string): AgentInfo | undefined {
    return this.snapshot?.agents.find((a) => a.id === id);
  }

  room(id: string): RoomInfo | undefined {
    return this.snapshot?.rooms.find((r) => r.id === id);
  }

  agentsInRoom(roomId: string): AgentInfo[] {
    return this.snapshot?.agents.filter((a) => a.roomId === roomId) ?? [];
  }

  reconnectNow(): void {
    void this.refresh();
  }

  /** Recarga cada REFRESH_MS mientras la página se ve; al volver a verse, recarga enseguida. */
  start(): void {
    void this.refresh();
    this.timer ??= setInterval(() => {
      if (!document.hidden) void this.refresh();
    }, REFRESH_MS);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) void this.refresh();
    });
  }

  /** Lee las rutas existentes y publica el resultado. Se llama también después de cada acción. */
  async refresh(): Promise<void> {
    let loaded: Loaded;
    try {
      loaded = await this.load();
    } catch (err) {
      this.lastError = message(err);
      this.nextRetryAt = this.now() + REFRESH_MS;
      this.setConnection("closed");
      return;
    }
    this.lastError = null;
    this.nextRetryAt = null;
    this.publish({ ...loaded, now: this.now() });
    this.setConnection("open");
  }

  private publish(data: LifeosData): void {
    this.rev += 1;
    const snapshot = toSnapshot(data, this.rev, this.snapshot ?? undefined);
    const feed = toFeed(data.history, snapshot).sort((a, b) => a.activity.at - b.activity.at);
    const fresh = this.seenFeed === null ? [] : feed.filter((f) => !this.seenFeed?.has(f.id));
    this.seenFeed = new Set(feed.map((f) => f.id));
    this.snapshot = snapshot;
    this.feed = feed;
    this.emit("snapshot", snapshot);
    this.emit("feed", feed);
    for (const item of fresh) this.emit("notice", this.notice(item));
  }

  private notice(item: FeedItem): Notice {
    const level = item.activity.error === true ? "warn" : "info";
    return { id: item.id, level, text: `${item.agentName}: ${item.activity.text}`, agentId: item.agentId, roomId: item.roomId, at: item.activity.at };
  }

  private setConnection(state: ConnectionState): void {
    if (this.connection === state) return;
    this.connection = state;
    this.emit("connection", state);
  }

  private emit<K extends keyof SourceEvents>(event: K, value: SourceEvents[K]): void {
    for (const cb of this.listeners[event] as Set<Listener<K>>) cb(value);
  }
}
