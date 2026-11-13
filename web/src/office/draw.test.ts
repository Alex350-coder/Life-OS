// Dibujo de la Oficina (ADR-0019) con un contexto 2D falso que registra las llamadas: jsdom no
// tiene canvas. Comprueba dónde quedan los personajes y qué se pinta, no los píxeles.
import { describe, expect, it } from "vitest";
import type { Briefing, PlanBlock } from "../api/types";
import { drawSeason, sparkle } from "./decor";
import { drawOffice, hitAt, WIDTH } from "./draw";
import { STILL_FRAME } from "./pause";
import { buildScene, type Scene } from "./scene";
import type { Season } from "./season";

interface Recorder {
  ctx: CanvasRenderingContext2D;
  rects: { color: string; x: number; y: number }[];
  texts: string[];
}

function recorder(): Recorder {
  const rects: Recorder["rects"] = [];
  const texts: string[] = [];
  let fillStyle: unknown = "";
  const ctx = {
    imageSmoothingEnabled: true,
    font: "",
    get fillStyle() {
      return fillStyle;
    },
    set fillStyle(value: unknown) {
      fillStyle = value;
    },
    fillRect(x: number, y: number) {
      rects.push({ color: String(fillStyle), x, y });
    },
    fillText(text: string) {
      texts.push(text);
    },
    measureText(text: string) {
      return { width: text.length * 6 };
    },
    createLinearGradient() {
      return { addColorStop() {} };
    },
  };
  return { ctx: ctx as unknown as CanvasRenderingContext2D, rects, texts };
}

const block = (kind: PlanBlock["kind"], title: string, start: string, end: string): PlanBlock => ({
  kind,
  title,
  task_id: kind === "trabajo" ? "t-aaaaaa" : null,
  day: "2026-11-09",
  span: `${start.slice(11, 16)}–${end.slice(11, 16)}`,
  start,
  end,
  minutes: 60,
});

const WORK = block("trabajo", "Informe", "2026-11-09T11:00:00-05:00", "2026-11-09T12:00:00-05:00");
const LATER = block("trabajo", "Guitarra", "2026-11-09T14:00:00-05:00", "2026-11-09T15:00:00-05:00");

function day(overrides: Partial<Briefing> = {}): Briefing {
  return {
    day: "2026-11-09",
    zone: "America/Lima",
    approved: true,
    readable: true,
    current: null,
    upcoming: LATER,
    resting: null,
    blocks: [WORK, LATER],
    work: [WORK, LATER],
    protected: [],
    checkins: 1,
    adjustments: [],
    rejected_adjustments: 0,
    ...overrides,
  };
}

const NOON = new Date("2026-11-09T12:30:00-05:00");

function scene(overrides: Partial<Briefing> = {}, at = NOON): Scene {
  return buildScene(day(overrides), [], at);
}

function hitFor(hits: ReturnType<typeof drawOffice>, id: string) {
  const found = hits.find((hit) => hit.id === id);
  if (found === undefined) throw new Error(`sin ${id}`);
  return found;
}

describe("dibujo de la oficina", () => {
  it("en el tiempo libre el Jefe y el Coach están en el sofá y uno habla", () => {
    const { ctx, texts } = recorder();
    const hits = drawOffice(ctx, scene(), STILL_FRAME, "12:30", []);
    expect(hitFor(hits, "jefe").x).toBeGreaterThan(500);
    expect(hitFor(hits, "coach").x).toBeGreaterThan(500);
    expect(hitFor(hits, "tu").x).toBeLessThan(200); // el propietario sigue en su puesto
    expect(texts).toContain("Hoy van 1 check-in(s)");
    expect(texts).toContain("12:30");
  });

  it("mientras el propietario trabaja nadie pasea y se ven sus globos", () => {
    const { ctx, texts } = recorder();
    const hits = drawOffice(ctx, scene({ current: WORK }), 5, "11:30", []);
    expect(hitFor(hits, "jefe").x).toBeLessThan(500);
    expect(texts).toContain("Plan aprobado");
    expect(texts).toContain("Informe");
  });

  it("a mitad de camino un paseante está entre su puesto y el sofá, sin globo", () => {
    const { ctx, texts } = recorder();
    const hits = drawOffice(ctx, scene(), STILL_FRAME / 2, "12:30", []);
    const x = hitFor(hits, "jefe").x;
    expect(x).toBeGreaterThan(150);
    expect(x).toBeLessThan(510);
    expect(texts).not.toContain("El plan de hoy sigue en pie");
  });

  it("una reacción sustituye al globo y añade destellos", () => {
    const plain = recorder();
    drawOffice(plain.ctx, scene({ current: WORK }), 5, "11:30", []);
    const celebrating = recorder();
    drawOffice(celebrating.ctx, scene({ current: WORK }), 5, "11:30", [{ who: "coach", text: "¡Check-in registrado!" }]);
    expect(celebrating.texts).toContain("¡Check-in registrado!");
    expect(celebrating.rects.length).toBe(plain.rects.length + 6);
  });

  it("de noche hay estrellas en la ventana", () => {
    const night = recorder();
    drawOffice(night.ctx, scene({}, new Date("2026-11-09T22:00:00-05:00")), 1, "22:00", []);
    expect(night.rects.some((r) => r.color === "#ffffff" && r.y < 60 && r.x >= 250 && r.x < 400)).toBe(true);
  });

  it("el clic encuentra al personaje y fuera de todos no hay nadie", () => {
    const { ctx } = recorder();
    const hits = drawOffice(ctx, scene(), STILL_FRAME, "12:30", []);
    const tu = hitFor(hits, "tu");
    expect(hitAt(hits, tu.x + 1, tu.y + 1)).toBe("tu");
    expect(hitAt(hits, WIDTH + 10, 0)).toBeNull();
  });
});

describe("decoración por estación", () => {
  it.each<Season>(["verano", "otono", "invierno", "primavera"])("pinta la planta y su detalle en %s", (season) => {
    const { ctx, rects } = recorder();
    drawSeason(ctx, season, 10);
    expect(rects.length).toBeGreaterThan(1);
  });

  it("las hojas cambian de color con la estación", () => {
    const autumn = recorder();
    drawSeason(autumn.ctx, "otono", 0);
    const spring = recorder();
    drawSeason(spring.ctx, "primavera", 0);
    expect(autumn.rects[0]?.color).not.toBe(spring.rects[0]?.color);
  });

  it("los destellos rodean al personaje", () => {
    const { ctx, rects } = recorder();
    sparkle(ctx, 100, 100, 0);
    expect(rects).toHaveLength(6);
    expect(rects.every((r) => Math.hypot(r.x - 100, r.y - 100) <= 31)).toBe(true);
  });
});
