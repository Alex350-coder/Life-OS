// Adaptador LifeOS → Habblaud (F11-T04, ADR-0022). Función pura: traduce las respuestas de las rutas
// existentes de la API al OfficeSnapshot que esperan el mundo y la interfaz de Habblaud. No guarda, no
// decide y no planifica: cada estado sale de una regla sobre datos reales (docs/specs/adaptacion-habblaud.md §4.3).
// Los campos propios de Claude Code (cuenta, sesión, modelo, tokens, shells, permisos, mensajes) quedan vacíos.
import type { Agents, Briefing, HistoryEntry, ImportedAgent, ReviewItem, Task } from "../api/types";
import { hash32 } from "../habblaud/shared/hash";
import type { Activity, ActivityKind, AgentInfo, AgentStatus, FeedItem, OfficeSnapshot, RoomInfo, TaskItem, TaskStatus } from "../habblaud/shared/types";

export const OWNER_ID = "propietario";
export const OWNER_ROOM = "tu-espacio";
export const JEFE_ROOM = "oficina:jefe";
export const COACH_ROOM = "oficina:coach";
const SPECIALIST_ROOM = "especialista:";
const ACCOUNT = "lifeos";
const OWNER_TASKS = 5;
const EMPTY_STATS = { toolCalls: 0, tokensIn: 0, tokensOut: 0, subagents: 0 } as const;

/** Lo que el store reúne de las rutas existentes antes de traducirlo. */
export interface LifeosData {
  /** null: no hay plan para hoy (GET /api/hoy responde 409 hasta que apruebas la semana). */
  today: Briefing | null;
  agents: Agents;
  proposals: ReviewItem[];
  upcoming: Task[];
  history: HistoryEntry[];
  now: number;
}

interface Presence {
  status: AgentStatus;
  waitingFor?: string;
  text: string;
  kind: ActivityKind;
  icon: string;
}

const PENDING = "pendiente";

function room(id: string, name: string, slot: number, now: number): RoomInfo {
  return { id, name, path: "", slot, seed: hash32(id), createdAt: now };
}

function specialistRoom(spec: ImportedAgent): string {
  return SPECIALIST_ROOM + spec.id;
}

function taskStatus(status: string): TaskStatus {
  if (status === "en_curso") return "in_progress";
  if (status === "completada" || status === "hecha") return "completed";
  return "pending";
}

function taskItems(tasks: Task[]): TaskItem[] {
  return tasks.filter((t) => t.id !== null).map((t) => ({ id: t.id ?? "", title: t.title, status: taskStatus(t.status) }));
}

const NO_PLAN = "Sin plan para hoy";

function ownerPresence(today: Briefing | null): Presence {
  if (today === null) return { status: "idle", text: NO_PLAN, kind: "wait", icon: "🗓️" };
  if (today.current?.kind === "trabajo") {
    return { status: "working", text: `Trabajando en ${today.current.title}`, kind: "edit", icon: "✍️" };
  }
  if (today.resting !== null) return { status: "idle", text: `Descanso: ${today.resting.title}`, kind: "wait", icon: "☕" };
  if (today.current !== null) return { status: "idle", text: `Compromiso: ${today.current.title}`, kind: "other", icon: "📌" };
  const next = today.upcoming === null ? "Nada más hoy" : `Luego: ${today.upcoming.title}`;
  return { status: "idle", text: next, kind: "wait", icon: "🕒" };
}

function jefePresence(today: Briefing | null): Presence {
  if (today === null) {
    return { status: "waiting", waitingFor: "proponer y aprobar la semana", text: NO_PLAN, kind: "plan", icon: "📋" };
  }
  if (!today.approved) {
    return { status: "waiting", waitingFor: "aprobar el plan de hoy", text: "Plan sin aprobar", kind: "plan", icon: "📋" };
  }
  const count = today.adjustments.length;
  if (count > 0) {
    const what = count === 1 ? "revisar 1 ajuste del Coach" : `revisar ${count} ajustes del Coach`;
    return { status: "waiting", waitingFor: what, text: "Ajustes pendientes", kind: "plan", icon: "📋" };
  }
  return { status: "idle", text: "Plan aprobado", kind: "plan", icon: "✅" };
}

function coachPresence(today: Briefing | null): Presence {
  if (today === null) return { status: "idle", text: NO_PLAN, kind: "communicate", icon: "💬" };
  const text = today.checkins === 1 ? "1 check-in hoy" : `${today.checkins} check-ins hoy`;
  return { status: "idle", text, kind: "communicate", icon: "💬" };
}

function specialistPresence(spec: ImportedAgent, proposals: ReviewItem[]): Presence {
  if (spec.state === "desactivado") return { status: "offline", text: "Desactivado", kind: "other", icon: "⏸️" };
  const pending = proposals.filter((p) => p.area === spec.area && p.status === PENDING).length;
  if (pending > 0) {
    const what = pending === 1 ? "revisar 1 propuesta" : `revisar ${pending} propuestas`;
    return { status: "waiting", waitingFor: what, text: "Propuestas pendientes", kind: "ask", icon: "📝" };
  }
  return { status: "idle", text: spec.state === "activo" ? "Activo" : `Estado: ${spec.state}`, kind: "other", icon: "🗂️" };
}

interface Seat {
  id: string;
  name: string;
  role: string;
  roomId: string;
  presence: Presence;
  tasks: TaskItem[];
}

function agentInfo(seat: Seat, now: number, previous: OfficeSnapshot | undefined): AgentInfo {
  const before = previous?.agents.find((a) => a.id === seat.id);
  const since = before !== undefined && before.status === seat.presence.status ? before.statusSince : now;
  const p = seat.presence;
  const activity: Activity = { id: `${seat.id}:${p.text}`, kind: p.kind, icon: p.icon, text: p.text, at: since };
  const seed = hash32(seat.id);
  const info: AgentInfo = {
    id: seat.id,
    kind: "main",
    roomId: seat.roomId,
    name: seat.name,
    look: seed % 2 === 0 ? "f" : "m",
    role: seat.role,
    sessionId: "",
    account: ACCOUNT,
    status: p.status,
    activity,
    recent: [activity],
    tasks: seat.tasks,
    startedAt: before?.startedAt ?? now,
    lastEventAt: now,
    statusSince: since,
    stats: { ...EMPTY_STATS },
    seed,
  };
  return p.waitingFor === undefined ? info : { ...info, waitingFor: p.waitingFor };
}

/** El snapshot del edificio a partir de los datos reales. `previous` mantiene `statusSince` estable. */
export function toSnapshot(data: LifeosData, rev: number, previous?: OfficeSnapshot): OfficeSnapshot {
  const { today, agents, proposals, upcoming, now } = data;
  const specialists = agents.imported ?? [];
  const name = (id: string, fallback: string) => agents.agents.find((a) => a.id === id)?.name ?? fallback;
  const rooms = [
    room(OWNER_ROOM, "Tu espacio", 0, now),
    room(JEFE_ROOM, "Oficina del Jefe", 1, now),
    room(COACH_ROOM, "Oficina del Coach", 2, now),
    ...specialists.map((s, i) => room(specialistRoom(s), s.name, 3 + i, now)),
  ];
  const seats: Seat[] = [
    { id: OWNER_ID, name: "Tú", role: "Propietario", roomId: OWNER_ROOM, presence: ownerPresence(today), tasks: taskItems(upcoming.slice(0, OWNER_TASKS)) },
    { id: "jefe", name: name("jefe", "Jefe"), role: "Planificador", roomId: JEFE_ROOM, presence: jefePresence(today), tasks: [] },
    { id: "coach", name: name("coach", "Coach"), role: "Acompañante", roomId: COACH_ROOM, presence: coachPresence(today), tasks: [] },
    ...specialists.map((s) => ({
      id: s.id,
      name: s.name,
      role: "Especialista",
      roomId: specialistRoom(s),
      presence: specialistPresence(s, proposals),
      tasks: taskItems(upcoming.filter((t) => t.area !== null && t.area === s.area)),
    })),
  ];
  return {
    rev,
    serverTime: now,
    rooms,
    agents: seats.map((seat) => agentInfo(seat, now, previous)),
    accounts: [],
    meta: { demo: false, sources: [], startedAt: previous?.meta.startedAt ?? now, version: "lifeos" },
  };
}

const ACTOR_ALIASES: Readonly<Record<string, string>> = { propietario: OWNER_ID };

/** La auditoría real (GET /api/historial) como actividad del edificio; los intentos no cuentan. */
export function toFeed(history: HistoryEntry[], snapshot: OfficeSnapshot): FeedItem[] {
  const items: FeedItem[] = [];
  for (const e of history) {
    if (e.result === "intento") continue;
    const agent = snapshot.agents.find((a) => a.id === (ACTOR_ALIASES[e.actor] ?? e.actor));
    if (agent === undefined) continue;
    const roomName = snapshot.rooms.find((r) => r.id === agent.roomId)?.name ?? "";
    const at = Date.parse(e.ts);
    const error = e.result === "rechazado" || e.result === "error";
    const activity: Activity = {
      id: `${e.ts}:${e.actor}:${e.action}:${e.target}`,
      kind: error ? "error" : "other",
      icon: error ? "⚠️" : "•",
      text: e.action.replaceAll("_", " "),
      at: Number.isNaN(at) ? 0 : at,
      ...(error ? { error: true } : {}),
    };
    items.push({ id: activity.id, agentId: agent.id, roomId: agent.roomId, agentName: agent.name, roomName, activity });
  }
  return items;
}
