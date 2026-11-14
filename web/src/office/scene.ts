// Estado de la oficina (docs/specs/interfaz-grafica.md): una función pura que traduce los datos
// reales de la API en lo que se dibuja. La escena no inventa nada ni decide nada: solo muestra.
import type { Agent, Briefing, PlanBlock } from "../api/types";

export type Light = "noche" | "amanecer" | "dia" | "atardecer";
export type Pose = "trabajando" | "descansando" | "esperando" | "pensando";
export type Tab = "hoy" | "semana" | "tareas" | "historial" | "agentes";

export interface Character {
  id: string;
  name: string;
  role: string;
  pose: Pose;
  bubble: string; // lo que dice el globo (texto corto)
  status: string; // frase completa para la lista accesible
  desk: number; // puesto en la oficina (0 = tú, 1 = Jefe, 2 = Coach, 3… especialistas)
  opens: Tab; // qué vista abre al hacer clic
}

export interface Scene {
  light: Light;
  characters: Character[];
  progress: { done: number; total: number }; // minutos de trabajo ya pasados / del día
  headline: string;
}

const NIGHT_END = 6;
const DAWN_END = 8;
const DUSK_START = 18;
const NIGHT_START = 20;
const MAX_BUBBLE = 28;
const CORE_AGENTS = new Set(["jefe", "coach"]);

export function lightAt(hour: number): Light {
  if (hour < NIGHT_END || hour >= NIGHT_START) return "noche";
  if (hour < DAWN_END) return "amanecer";
  if (hour >= DUSK_START) return "atardecer";
  return "dia";
}

function short(text: string): string {
  return text.length <= MAX_BUBBLE ? text : `${text.slice(0, MAX_BUBBLE - 1)}…`;
}

function workDone(work: PlanBlock[], now: Date): { done: number; total: number } {
  let done = 0;
  let total = 0;
  for (const block of work) {
    total += block.minutes;
    const start = Date.parse(block.start);
    const end = Date.parse(block.end);
    if (Number.isNaN(start) || Number.isNaN(end)) continue;
    if (now.getTime() >= end) done += block.minutes;
    else if (now.getTime() > start) done += Math.round((now.getTime() - start) / 60_000);
  }
  return { done: Math.min(done, total), total };
}

function owner(day: Briefing | null): Character {
  const base = { id: "tu", name: "Tú", role: "propietario", desk: 0, opens: "hoy" as const };
  if (day === null) {
    return { ...base, pose: "esperando", bubble: "…", status: "Sin datos del día todavía." };
  }
  if (day.resting !== null) {
    const what = `${day.resting.title} (${day.resting.span})`;
    return { ...base, pose: "descansando", bubble: short(day.resting.title), status: `Descansando: ${what}.` };
  }
  if (day.current !== null && day.current.kind === "trabajo") {
    const what = `${day.current.title} (${day.current.span})`;
    return { ...base, pose: "trabajando", bubble: short(day.current.title), status: `Trabajando en ${what}.` };
  }
  if (day.current !== null) {
    return { ...base, pose: "esperando", bubble: short(day.current.title), status: `Compromiso: ${day.current.title}.` };
  }
  const next = day.upcoming === null ? "nada más hoy" : `luego: ${day.upcoming.title} (${day.upcoming.span})`;
  return { ...base, pose: "esperando", bubble: "Libre", status: `Tiempo libre; ${next}.` };
}

function jefe(day: Briefing | null): Character {
  const base = { id: "jefe", name: "Jefe", role: "planificador", desk: 1, opens: "semana" as const };
  if (day === null) return { ...base, pose: "esperando", bubble: "…", status: "Esperando datos." };
  if (day.adjustments.length > 0) {
    const count = day.adjustments.length;
    return { ...base, pose: "pensando", bubble: `${count} ajuste(s) del Coach`, status: `Tiene ${count} propuesta(s) del Coach para replanificar.` };
  }
  if (!day.approved) {
    return { ...base, pose: "pensando", bubble: "Plan sin aprobar", status: "El plan de hoy no está aprobado: revisa la semana." };
  }
  return { ...base, pose: "trabajando", bubble: "Plan aprobado", status: "El plan de hoy está aprobado." };
}

function coach(day: Briefing | null): Character {
  const base = { id: "coach", name: "Coach", role: "acompañante", desk: 2, opens: "hoy" as const };
  if (day === null) return { ...base, pose: "esperando", bubble: "…", status: "Esperando datos." };
  if (day.resting !== null) {
    return { ...base, pose: "descansando", bubble: "¡A descansar!", status: "Te recuerda descansar sin culpa." };
  }
  if (day.current !== null && day.current.kind === "trabajo") {
    return { ...base, pose: "trabajando", bubble: "¡Tú puedes!", status: `Te acompaña en ${day.current.title}. Check-ins hoy: ${day.checkins}.` };
  }
  return { ...base, pose: "esperando", bubble: `${day.checkins} check-in(s)`, status: `Check-ins de hoy: ${day.checkins}.` };
}

function specialist(agent: Agent, desk: number): Character {
  const active = agent.status === "activo";
  return {
    id: agent.id,
    name: agent.name,
    role: "especialista",
    pose: active ? "trabajando" : "esperando",
    bubble: short(active ? agent.purpose : agent.status),
    status: `${agent.name}: ${agent.status}. ${agent.purpose}`,
    desk,
    opens: "agentes",
  };
}

export function buildScene(day: Briefing | null, agents: Agent[], now: Date): Scene {
  const extra = agents.filter((agent) => !CORE_AGENTS.has(agent.id)).map((agent, i) => specialist(agent, 3 + i));
  const characters = [owner(day), jefe(day), coach(day), ...extra];
  const progress = day === null ? { done: 0, total: 0 } : workDone(day.work, now);
  const headline =
    day === null ? "Cargando la oficina…"
    : progress.total === 0 ? `Hoy ${day.day}: sin bloques de trabajo`
    : `Hoy ${day.day}: ${Math.round((progress.done / progress.total) * 100)} % del trabajo del día`;
  return { light: lightAt(now.getHours()), characters, progress, headline };
}
