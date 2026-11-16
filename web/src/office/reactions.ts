// Reacciones a eventos reales (F9-T04): se comparan dos lecturas seguidas de /api/hoy. Solo
// celebran lo cumplido; nunca hay reacción por un día flojo (spec del Coach: sin culpa).
import type { Briefing } from "../api/types";

export interface Reaction {
  who: string;
  text: string;
}

export const REACTION_MS = 4000;

function blockFinished(prev: Briefing, next: Briefing, now: Date): boolean {
  const block = prev.current;
  if (block === null || block.kind !== "trabajo") return false;
  const end = Date.parse(block.end);
  return !Number.isNaN(end) && now.getTime() >= end && next.current?.start !== block.start;
}

export function detectReactions(prev: Briefing | null, next: Briefing | null, now: Date): Reaction[] {
  if (prev === null || next === null || prev.day !== next.day) return [];
  const found: Reaction[] = [];
  if (!prev.approved && next.approved) found.push({ who: "jefe", text: "¡Plan aprobado!" });
  if (next.checkins > prev.checkins) found.push({ who: "coach", text: "¡Check-in registrado!" });
  if (next.adjustments.length > prev.adjustments.length) {
    found.push({ who: "jefe", text: "Nueva propuesta del Coach" });
  }
  if (blockFinished(prev, next, now)) found.push({ who: "tu", text: "¡Bloque terminado!" });
  return found;
}
