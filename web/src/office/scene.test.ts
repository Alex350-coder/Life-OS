import { describe, expect, it } from "vitest";
import type { Agent, Briefing, PlanBlock } from "../api/types";
import { buildScene, lightAt } from "./scene";

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

const WORK_1 = block("trabajo", "Informe de redes", "2026-11-09T11:00:00-05:00", "2026-11-09T12:00:00-05:00", 60);
const WORK_2 = block("trabajo", "Guitarra", "2026-11-09T14:00:00-05:00", "2026-11-09T15:00:00-05:00", 60);
const LUNCH = block("protegido", "Almuerzo", "2026-11-09T13:00:00-05:00", "2026-11-09T14:00:00-05:00", 60);

function day(overrides: Partial<Briefing> = {}): Briefing {
  return {
    day: "2026-11-09",
    zone: "America/Lima",
    approved: true,
    readable: true,
    current: null,
    upcoming: null,
    resting: null,
    blocks: [WORK_1, LUNCH, WORK_2],
    work: [WORK_1, WORK_2],
    protected: [LUNCH],
    checkins: 2,
    adjustments: [],
    rejected_adjustments: 0,
    ...overrides,
  };
}

describe("oficina: escena a partir de datos reales", () => {
  it("la luz sigue la hora del día", () => {
    expect(lightAt(3)).toBe("noche");
    expect(lightAt(7)).toBe("amanecer");
    expect(lightAt(12)).toBe("dia");
    expect(lightAt(19)).toBe("atardecer");
    expect(lightAt(22)).toBe("noche");
  });

  it("trabajando en un bloque: tú y el Coach en el escritorio", () => {
    const scene = buildScene(day({ current: WORK_1 }), [], new Date("2026-11-09T11:30:00-05:00"));
    const [tu, jefe, coach] = scene.characters;
    expect(tu?.pose).toBe("trabajando");
    expect(tu?.status).toContain("Informe de redes");
    expect(jefe?.bubble).toBe("Plan aprobado");
    expect(coach?.pose).toBe("trabajando");
    expect(scene.progress).toEqual({ done: 30, total: 120 });
    expect(scene.headline).toContain("25 %");
  });

  it("en tiempo protegido todos descansan", () => {
    const scene = buildScene(day({ resting: LUNCH }), [], new Date("2026-11-09T13:30:00-05:00"));
    expect(scene.characters[0]?.pose).toBe("descansando");
    expect(scene.characters[2]?.bubble).toBe("¡A descansar!");
    expect(scene.progress.done).toBe(60);
  });

  it("el Jefe avisa de un plan sin aprobar o de ajustes pendientes", () => {
    expect(buildScene(day({ approved: false }), [], new Date()).characters[1]?.pose).toBe("pensando");
    const withAdjustment = day({ adjustments: [{ day: "2026-11-09", at: "12:10", kind: "bloque_perdido", text: "x" }] });
    expect(buildScene(withAdjustment, [], new Date()).characters[1]?.bubble).toBe("1 ajuste(s) del Coach");
  });

  it("los especialistas registrados ocupan puestos nuevos; Jefe y Coach no se duplican", () => {
    const agent = (id: string, status: string): Agent =>
      ({ id, name: id, status, purpose: "Universidad y tareas del ciclo" }) as Agent;
    const scene = buildScene(day(), [agent("jefe", "activo"), agent("universidad", "activo")], new Date());
    expect(scene.characters.map((c) => c.id)).toEqual(["tu", "jefe", "coach", "universidad"]);
    expect(scene.characters[3]?.desk).toBe(3);
    expect(scene.characters[3]?.opens).toBe("agentes");
  });

  it("sin datos todavía, la escena espera sin inventar", () => {
    const scene = buildScene(null, [], new Date());
    expect(scene.headline).toBe("Cargando la oficina…");
    expect(scene.characters.every((c) => c.pose === "esperando")).toBe(true);
  });
});
