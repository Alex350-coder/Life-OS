// Paseos y conversaciones (F9-T04). Cuando el propietario descansa o tiene tiempo libre, el Jefe
// y el Coach van al sofá y charlan. Lo que dicen sale de los datos reales del día; un agente con
// algo pendiente (plan sin aprobar, ajustes) se queda en su puesto para que se vea.
import type { Briefing } from "../api/types";
import type { Character } from "./scene";

export interface Line {
  who: string;
  text: string;
}

export interface Pause {
  walkers: string[];
  lines: Line[];
}

// Ciclo de un paseo en fotogramas (a 12 por segundo, unos 24 s): ir, charlar y volver.
export const WALK_FRAMES = 36;
export const STAY_FRAMES = 216;
export const LINE_FRAMES = 54;
export const CYCLE_FRAMES = WALK_FRAMES * 2 + STAY_FRAMES;
// Fotograma fijo con movimiento reducido: ya en el sofá, con la primera frase.
export const STILL_FRAME = WALK_FRAMES;

const MAX_LINE = 30;
const WALKERS = new Set(["jefe", "coach"]);

function short(text: string): string {
  return text.length <= MAX_LINE ? text : `${text.slice(0, MAX_LINE - 1)}…`;
}

function isFree(day: Briefing): boolean {
  return day.resting !== null || day.current === null;
}

function linesFor(day: Briefing, walkers: string[]): Line[] {
  const lines: Line[] = [];
  if (walkers.includes("coach")) {
    const text = day.checkins > 0 ? `Hoy van ${day.checkins} check-in(s)` : "¿Qué tal va el día?";
    lines.push({ who: "coach", text });
  }
  if (walkers.includes("jefe")) {
    lines.push({ who: "jefe", text: "El plan de hoy sigue en pie" });
    const next = day.upcoming === null ? "Nada más por hoy" : short(`Luego: ${day.upcoming.title}`);
    lines.push({ who: "jefe", text: next });
  }
  if (walkers.includes("coach") && day.resting !== null) {
    lines.push({ who: "coach", text: "El descanso también cuenta" });
  }
  return lines;
}

export function pauseFor(day: Briefing | null, characters: Character[]): Pause | null {
  if (day === null || !day.readable || !day.approved || !isFree(day)) return null;
  const walkers = characters.filter((c) => WALKERS.has(c.id) && c.pose !== "pensando").map((c) => c.id);
  if (walkers.length === 0) return null;
  return { walkers, lines: linesFor(day, walkers) };
}

export interface WalkPhase {
  progress: number; // 0 en el puesto, 1 en el sofá
  talking: boolean;
  line: number;
}

export function walkPhase(frame: number): WalkPhase {
  const f = ((frame % CYCLE_FRAMES) + CYCLE_FRAMES) % CYCLE_FRAMES;
  if (f < WALK_FRAMES) return { progress: f / WALK_FRAMES, talking: false, line: 0 };
  if (f < WALK_FRAMES + STAY_FRAMES) {
    return { progress: 1, talking: true, line: Math.floor((f - WALK_FRAMES) / LINE_FRAMES) };
  }
  return { progress: 1 - (f - WALK_FRAMES - STAY_FRAMES) / WALK_FRAMES, talking: false, line: 0 };
}

// Frase que se ve en este fotograma, o null si nadie habla.
export function currentLine(pause: Pause, frame: number): Line | null {
  const phase = walkPhase(frame);
  if (!phase.talking || pause.lines.length === 0) return null;
  return pause.lines[phase.line % pause.lines.length] ?? null;
}
