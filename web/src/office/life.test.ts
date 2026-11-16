// Fase 9 (F9-T04): estación, paseos, reacciones, panel «Mi día» y ritmo de la animación.
// Funciones puras sobre datos inventados con la forma real de la API.
import { describe, expect, it } from "vitest";
import type { Briefing, Indicators, PlanBlock } from "../api/types";
import { frameInterval, FRAME_MS, isLowEnd, LOW_END_FRAME_MS } from "./motion";
import { myDay } from "./myDay";
import { currentLine, CYCLE_FRAMES, pauseFor, STILL_FRAME, WALK_FRAMES, walkPhase } from "./pause";
import { detectReactions } from "./reactions";
import { buildScene } from "./scene";
import { isSouthern, seasonAt } from "./season";

const block = (kind: PlanBlock["kind"], title: string, start: string, end: string, minutes: number): PlanBlock => ({
  kind,
  title,
  task_id: kind === "trabajo" ? "t-aaaaaa" : null,
  day: "2026-11-09",
  span: `${start.slice(11, 16)}–${end.slice(11, 16)}`,
  start,
  end,
  minutes,
});

const WORK = block("trabajo", "Informe de redes", "2026-11-09T11:00:00-05:00", "2026-11-09T12:00:00-05:00", 60);
const LATER = block("trabajo", "Guitarra", "2026-11-09T14:00:00-05:00", "2026-11-09T15:00:00-05:00", 60);
const LUNCH = block("protegido", "Almuerzo", "2026-11-09T13:00:00-05:00", "2026-11-09T14:00:00-05:00", 60);

function day(overrides: Partial<Briefing> = {}): Briefing {
  return {
    day: "2026-11-09",
    zone: "America/Lima",
    approved: true,
    readable: true,
    current: null,
    upcoming: LATER,
    resting: null,
    blocks: [WORK, LUNCH, LATER],
    work: [WORK, LATER],
    protected: [LUNCH],
    checkins: 1,
    adjustments: [],
    rejected_adjustments: 0,
    ...overrides,
  };
}

function indicators(overrides: Partial<Indicators> = {}): Indicators {
  return {
    start: "2026-10-13",
    end: "2026-11-09",
    streak: 3,
    focus_max_minutes: 240,
    deliveries: { due: 0, completed: 0, forgotten: 0, percent: null },
    adherence: { closed_days: 0, answered: 0, percent: null },
    usage: { active_days: 0, days_per_week: 0 },
    acceptance: { proposed: 0, approved: 0, minor_changes: 0, percent: null },
    costs: [],
    notifications: { sent: 0, failed: 0, skipped: 0, days_with_notifications: 0, max_per_day: 0, per_active_day: null, by_kind: [] },
    not_measured: [],
    quality: { malformed_log_lines: 0, unreadable_notes: 0, invalid_task_lines: 0, unreadable_closings: 0 },
    ...overrides,
  };
}

const NOON = new Date("2026-11-09T12:30:00-05:00");

describe("estación", () => {
  it("Lima está en el hemisferio sur: noviembre es primavera y enero verano", () => {
    expect(isSouthern("America/Lima")).toBe(true);
    expect(seasonAt(new Date(2026, 10, 13), "America/Lima")).toBe("primavera");
    expect(seasonAt(new Date(2027, 0, 10), "America/Lima")).toBe("verano");
    expect(seasonAt(new Date(2026, 6, 1), "America/Lima")).toBe("invierno");
  });

  it("en el norte las estaciones van al revés", () => {
    expect(isSouthern("Europe/Madrid")).toBe(false);
    expect(seasonAt(new Date(2026, 10, 13), "Europe/Madrid")).toBe("otono");
    expect(seasonAt(new Date(2026, 6, 1), "Europe/Madrid")).toBe("verano");
  });

  it("no confunde una zona que solo empieza igual", () => {
    expect(isSouthern("America/Limaxx")).toBe(false);
    expect(isSouthern("America/Argentina/Cordoba")).toBe(true);
    expect(isSouthern("America/Los_Angeles")).toBe(false);
  });
});

describe("paseos y conversaciones", () => {
  it("en tiempo libre el Jefe y el Coach charlan con datos reales", () => {
    const scene = buildScene(day(), [], NOON);
    const pause = pauseFor(day(), scene.characters);
    expect(pause?.walkers).toEqual(["jefe", "coach"]);
    expect(pause?.lines.map((line) => line.text)).toEqual([
      "Hoy van 1 check-in(s)",
      "El plan de hoy sigue en pie",
      "Luego: Guitarra",
    ]);
  });

  it("en un descanso el Coach recuerda que descansar también cuenta", () => {
    const resting = day({ resting: LUNCH, current: LUNCH });
    const pause = pauseFor(resting, buildScene(resting, [], NOON).characters);
    expect(pause?.lines.at(-1)).toEqual({ who: "coach", text: "El descanso también cuenta" });
  });

  it("nadie pasea mientras el propietario trabaja", () => {
    const working = day({ current: WORK });
    expect(pauseFor(working, buildScene(working, [], NOON).characters)).toBeNull();
  });

  it("con el plan sin aprobar el Jefe se queda en su puesto y no hay paseo", () => {
    const pending = day({ approved: false });
    expect(pauseFor(pending, buildScene(pending, [], NOON).characters)).toBeNull();
  });

  it("con ajustes pendientes el Jefe piensa en su puesto y solo pasea el Coach", () => {
    const adjustments = [{ kind: "bloque_perdido", task_id: "t-aaaaaa", text: "x" }] as unknown as Briefing["adjustments"];
    const busy = day({ adjustments });
    const pause = pauseFor(busy, buildScene(busy, [], NOON).characters);
    expect(pause?.walkers).toEqual(["coach"]);
    expect(pause?.lines.map((line) => line.who)).toEqual(["coach"]);
  });

  it("sin datos o con la nota ilegible no hay paseo", () => {
    expect(pauseFor(null, [])).toBeNull();
    const unreadable = day({ readable: false });
    expect(pauseFor(unreadable, buildScene(unreadable, [], NOON).characters)).toBeNull();
  });

  it("el ciclo va al sofá, charla y vuelve", () => {
    expect(walkPhase(0)).toEqual({ progress: 0, talking: false, line: 0 });
    expect(walkPhase(WALK_FRAMES / 2).progress).toBeCloseTo(0.5);
    expect(walkPhase(STILL_FRAME)).toEqual({ progress: 1, talking: true, line: 0 });
    expect(walkPhase(CYCLE_FRAMES - WALK_FRAMES / 2).progress).toBeCloseTo(0.5);
    expect(walkPhase(CYCLE_FRAMES)).toEqual(walkPhase(0));
    expect(walkPhase(-1).progress).toBeGreaterThan(0); // nunca un índice negativo
  });

  it("las frases se turnan mientras charlan", () => {
    const pause = { walkers: ["jefe", "coach"], lines: [{ who: "coach", text: "a" }, { who: "jefe", text: "b" }] };
    expect(currentLine(pause, 0)).toBeNull();
    expect(currentLine(pause, STILL_FRAME)?.text).toBe("a");
    expect(currentLine(pause, STILL_FRAME + 54)?.text).toBe("b");
    expect(currentLine({ walkers: ["jefe"], lines: [] }, STILL_FRAME)).toBeNull();
  });
});

describe("reacciones a eventos reales", () => {
  it("celebra un plan aprobado, un check-in y una propuesta nueva", () => {
    const adjustments = [{}] as unknown as Briefing["adjustments"];
    const found = detectReactions(day({ approved: false, checkins: 0 }), day({ adjustments }), NOON);
    expect(found.map((r) => r.who)).toEqual(["jefe", "coach", "jefe"]);
  });

  it("celebra un bloque de trabajo terminado", () => {
    const found = detectReactions(day({ current: WORK }), day({ current: null }), NOON);
    expect(found).toEqual([{ who: "tu", text: "¡Bloque terminado!" }]);
  });

  it("no reacciona si el bloque aún no terminó, ni entre días distintos, ni sin datos", () => {
    const early = new Date("2026-11-09T11:30:00-05:00");
    expect(detectReactions(day({ current: WORK }), day({ current: WORK }), early)).toEqual([]);
    expect(detectReactions(day({ checkins: 0 }), day({ day: "2026-11-10" }), NOON)).toEqual([]);
    expect(detectReactions(null, day(), NOON)).toEqual([]);
  });

  it("nunca castiga: menos check-ins o un plan que deja de estar aprobado no generan reacción", () => {
    expect(detectReactions(day({ checkins: 3 }), day({ checkins: 1, approved: false }), NOON)).toEqual([]);
  });
});

describe("panel Mi día", () => {
  it("resume el progreso, el límite de foco, la racha y lo siguiente", () => {
    const summary = myDay(day(), indicators(), NOON);
    expect(summary).toMatchObject({
      doneMinutes: 60,
      workMinutes: 120,
      focusMaxMinutes: 240,
      overLimit: false,
      streak: 3,
      checkins: 1,
      next: "Guitarra (14:00–15:00)",
    });
    expect(summary.cheer).toContain("3 días seguidos");
  });

  it("avisa si el trabajo del día supera foco_max_dia", () => {
    expect(myDay(day(), indicators({ focus_max_minutes: 90 }), NOON).overLimit).toBe(true);
  });

  it("sin indicadores no inventa la racha ni el límite", () => {
    const summary = myDay(day(), null, NOON);
    expect([summary.streak, summary.focusMaxMinutes, summary.overLimit]).toEqual([null, null, false]);
  });

  it("una racha en 0 anima sin culpa", () => {
    expect(myDay(null, indicators({ streak: 0 }), NOON).cheer).toBe("Cada cierre suma; el de hoy puede ser el primero.");
    expect(myDay(null, indicators({ streak: 1 }), NOON).cheer).toContain("1 día");
  });
});

describe("ritmo de la animación", () => {
  it("baja a 8 fotogramas por segundo en equipos modestos", () => {
    expect(isLowEnd({ deviceMemory: 2 })).toBe(true);
    expect(isLowEnd({ deviceMemory: 8, hardwareConcurrency: 2 })).toBe(false);
    expect(isLowEnd({ hardwareConcurrency: 4 })).toBe(true);
    expect(isLowEnd({})).toBe(false);
    expect(frameInterval({ hardwareConcurrency: 2 })).toBe(LOW_END_FRAME_MS);
    expect(frameInterval({ hardwareConcurrency: 16 })).toBe(FRAME_MS);
  });
});
