// Panel «Mi día» (F9-T04): progreso del día, foco frente a foco_max_dia y racha de cierres.
// Todo sale de /api/hoy y /api/indicadores; sin datos se dice, no se inventa.
import type { Briefing, Indicators } from "../api/types";
import { workDone } from "./scene";

interface MyDay {
  doneMinutes: number;
  workMinutes: number;
  focusMaxMinutes: number | null;
  overLimit: boolean;
  streak: number | null;
  checkins: number | null;
  next: string | null;
  cheer: string;
}

function cheer(streak: number | null): string {
  if (streak === null) return "Cada cierre cuenta.";
  if (streak === 0) return "Cada cierre suma; el de hoy puede ser el primero.";
  return streak === 1 ? "¡1 día cerrado! Sigue así." : `¡${streak} días seguidos cerrando el día!`;
}

export function myDay(day: Briefing | null, indicators: Indicators | null, now: Date): MyDay {
  const progress = day === null ? { done: 0, total: 0 } : workDone(day.work, now);
  const focusMaxMinutes = indicators?.focus_max_minutes ?? null;
  const streak = indicators?.streak ?? null;
  const upcoming = day?.upcoming ?? null;
  return {
    doneMinutes: progress.done,
    workMinutes: progress.total,
    focusMaxMinutes,
    overLimit: focusMaxMinutes !== null && progress.total > focusMaxMinutes,
    streak,
    checkins: day?.checkins ?? null,
    next: upcoming === null ? null : `${upcoming.title} (${upcoming.span})`,
    cheer: cheer(streak),
  };
}
