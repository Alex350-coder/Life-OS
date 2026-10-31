import { describe, expect, it } from "vitest";
import type { CheckInResult, PlanResult } from "../api/types";
import { block } from "../test/fakeApi";
import { coachReply, jefeReply } from "./chatFormat";

const checkin: CheckInResult = {
  kind: "micro_paso",
  message: "Empieza por algo pequeño.",
  micro_step: { action: "Abre el documento", minutes: 5 },
  adjustment: { day: "2026-11-02", at: "08:30", kind: "reducir carga hoy", text: "ajuste: reducir carga hoy" },
  path: "Diario/2026-11-02.md",
  capture: "00-Inbox/idea.md",
  ai_fallback: true,
  readable: false,
};

const plan: PlanResult = {
  accepted: true,
  status: "propuesto",
  path: "Semanal/2026-W45.md",
  proposal: {
    week: "2026-W45",
    origin: "heuristica",
    work: [block()],
    unplanned: [{ task_id: "t-bbbbbb", title: "Leer", minutes: 60, reason: "sin capacidad", suggestion: "aplazar" }],
    alerts: ["Viernes sin capacidad"],
    justification: "—",
  },
  conflicts: [],
};

describe("respuestas del chat", () => {
  it("muestra todo lo que devolvió el Coach", () => {
    expect(coachReply(checkin)).toEqual([
      "Empieza por algo pequeño.",
      "Micro-paso (5 min): Abre el documento",
      "Propuesta para el Jefe registrada: ajuste: reducir carga hoy. Revísala replanificando.",
      "Captura guardada en 00-Inbox/idea.md",
      "Aviso: la nota de hoy no sigue el formato del plan; no se usaron sus bloques.",
      "(El modelo no respondió bien; se usó la respuesta sin IA.)",
      "Registrado en Diario/2026-11-02.md",
    ]);
  });

  it("resume una propuesta del Jefe con lo que no cabe", () => {
    const lines = jefeReply(plan);
    expect(lines[0]).toContain("Plan propuesto para 2026-W45");
    expect(lines).toContain("No cabe: Leer · 60 min · sin capacidad → aplazar");
    expect(lines).toContain("Alerta: Viernes sin capacidad");
  });

  it("dice que un plan rechazado no se guardó", () => {
    const rejected = { ...plan, accepted: false, status: null, path: null, conflicts: [{ code: "solapamiento", message: "choca con la clase", day: null }] };
    expect(jefeReply(rejected)).toEqual([
      "Plan de 2026-W45 rechazado: 1 conflicto(s). No se guardó nada.",
      "[solapamiento] choca con la clase",
    ]);
  });

  it("confirma un plan aprobado", () => {
    expect(jefeReply({ ...plan, status: "aprobado" })[0]).toContain("aprobado y publicado");
  });
});
