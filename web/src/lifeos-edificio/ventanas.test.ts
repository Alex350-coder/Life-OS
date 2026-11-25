// Comportamiento de las ventanas del edificio con la API falsa (datos inventados, aislados en la prueba).
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ClosingPreview } from "../api/types";
import { briefing, fail, installFakeApi, ok, XSS } from "../test/fakeApi";
import { crearCoach } from "./ventanas/coach";
import { crearJefe } from "./ventanas/jefe";
import { crearTareas } from "./ventanas/tareas";
import type { Ventana } from "./ventana";

afterEach(() => vi.unstubAllGlobals());

function boton(w: Ventana, texto: string): HTMLButtonElement {
  const b = [...w.el.querySelectorAll("button")].find((x) => x.textContent === texto);
  if (b === undefined) throw new Error(`no hay botón «${texto}»`);
  return b;
}

const estado = (w: Ventana) => w.el.querySelector(".lo-status")?.textContent ?? "";

const PREVIA: ClosingPreview = { day: "2026-11-02", path: "Diario/2026-11-02.md", changes: [], unanswered: [], missed: [], closed: false };

describe("oficina del Coach", () => {
  it("«Confirmar cierre» solo se habilita después de la vista previa", async () => {
    const calls = installFakeApi({ "GET /api/hoy": ok(briefing()), "POST /api/cierre/vista-previa": ok(PREVIA), "POST /api/cierre": ok({ ...PREVIA, written: ["Diario/2026-11-02.md"], adjustments: [] }) });
    const w = crearCoach(() => undefined);
    w.open();
    await vi.waitFor(() => expect(w.el.querySelector(".lo-task")).not.toBeNull());
    const confirmar = boton(w, "Confirmar cierre");
    expect(confirmar.disabled).toBe(true);
    boton(w, "Ver vista previa").click();
    await vi.waitFor(() => expect(confirmar.disabled).toBe(false));
    confirmar.click();
    await vi.waitFor(() => expect(estado(w)).toBe("Cierre registrado."));
    expect(confirmar.disabled).toBe(true);
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(["/api/cierre/vista-previa", "/api/cierre"]);
  });

  it("cambiar una respuesta después de la vista previa obliga a verla de nuevo", async () => {
    installFakeApi({ "GET /api/hoy": ok(briefing()), "POST /api/cierre/vista-previa": ok(PREVIA) });
    const w = crearCoach(() => undefined);
    w.open();
    await vi.waitFor(() => expect(w.el.querySelector(".lo-task select")).not.toBeNull());
    const confirmar = boton(w, "Confirmar cierre");
    boton(w, "Ver vista previa").click();
    await vi.waitFor(() => expect(confirmar.disabled).toBe(false));
    const tipo = w.el.querySelector<HTMLSelectElement>(".lo-task select");
    if (tipo === null) throw new Error("sin selector");
    tipo.value = "hecha";
    tipo.dispatchEvent(new Event("input", { bubbles: true }));
    expect(confirmar.disabled).toBe(true);
  });

  it("sin plan para hoy (409) lo dice y no ofrece tareas que cerrar", async () => {
    installFakeApi({ "GET /api/hoy": fail(409, "rechazado", "no hay plan para 2026-11-02") });
    const w = crearCoach(() => undefined);
    w.open();
    await vi.waitFor(() => expect(w.el.textContent).toContain("Sin plan para hoy"));
    expect(w.el.querySelector(".lo-task")).toBeNull();
  });

  it("el check-in muestra la respuesta del Coach y refresca el edificio", async () => {
    const refrescar = vi.fn();
    installFakeApi({
      "GET /api/hoy": ok(briefing()),
      "POST /api/checkins": ok({ kind: "animo", message: "¡Bien!", micro_step: null, adjustment: null, path: "Diario/2026-11-02.md", capture: null, ai_fallback: false, readable: true }),
    });
    const w = crearCoach(refrescar);
    w.open();
    boton(w, "Enviar check-in").click();
    await vi.waitFor(() => expect(estado(w)).toBe("Check-in guardado."));
    expect(w.el.textContent).toContain("¡Bien!");
    expect(refrescar).toHaveBeenCalled();
  });
});

describe("oficina del Jefe", () => {
  it("un plan rechazado por el validador muestra sus conflictos", async () => {
    installFakeApi({
      "GET /api/semana": ok({ week: "2026-W45", zone: "America/Lima", path: null, status: null, origin: null, revision: 0, versions: 0, generated: null, justification: "", days: [], blocks: [], load: [] }),
      "POST /api/plan/aprobacion": fail(422, "plan_rechazado", "1 conflicto(s)", { conflicts: [{ code: "x", message: "el lunes supera el foco máximo", day: "2026-11-02" }] }),
    });
    const w = crearJefe(() => undefined);
    w.open();
    boton(w, "Aprobar el plan propuesto").click();
    await vi.waitFor(() => expect(estado(w)).toBe("1 conflicto(s): el lunes supera el foco máximo"));
    expect(w.el.querySelector(".lo-status--error")).not.toBeNull();
  });
});

describe("avisos", () => {
  it("al volver a abrir una ventana no queda el aviso anterior", async () => {
    installFakeApi({ "GET /api/tareas/proximas": ok([]) });
    const w = crearTareas(() => undefined);
    w.open();
    boton(w, "Capturar").click();
    await vi.waitFor(() => expect(estado(w)).toBe("Escribe algo para capturar."));
    w.close();
    w.open();
    expect(estado(w)).toBe("");
    expect(w.el.querySelector<HTMLElement>(".lo-status")?.hidden).toBe(true);
  });
});

describe("tu espacio", () => {
  it("la captura se envía, se limpia el campo y el texto de la bóveda nunca se interpreta como HTML", async () => {
    const calls = installFakeApi({
      "GET /api/tareas/proximas": ok([{ id: "t-cccccc", title: XSS, status: "propuesta", area: "universidad", effort: "30m", progress: null, priority: "media", due: "2026-11-05", scheduled: null, path: "x.md", archived: false }]),
      "POST /api/capturas": { status: 201, body: { ok: true, data: { path: "00-Inbox/idea.md" } } },
    });
    const w = crearTareas(() => undefined);
    w.open();
    await vi.waitFor(() => expect(w.el.textContent).toContain(XSS));
    expect(w.el.querySelector("img")).toBeNull();
    const input = w.el.querySelector<HTMLInputElement>("input");
    if (input === null) throw new Error("sin campo");
    input.value = "  idea nueva ";
    boton(w, "Capturar").click();
    await vi.waitFor(() => expect(estado(w)).toBe("Capturado en 00-Inbox/idea.md."));
    expect(input.value).toBe("");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ texto: "idea nueva" });
  });

  it("no envía una captura vacía", () => {
    const calls = installFakeApi({ "GET /api/tareas/proximas": ok([]) });
    const w = crearTareas(() => undefined);
    boton(w, "Capturar").click();
    expect(calls.filter((c) => c.method === "POST")).toEqual([]);
  });
});
