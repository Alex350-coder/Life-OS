// «Estilo para todos los elementos»: cada clase propia (lo-*) que usan las ventanas tiene su regla en edificio.css,
// y ningún control se queda con el aspecto del navegador. La E2E lo comprueba además con el estilo calculado.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import { block, briefing, installFakeApi, ok, week } from "../test/fakeApi";
import { crearVentanas } from "./interfaz";

afterEach(() => vi.unstubAllGlobals());

const CSS = readFileSync(resolve(process.cwd(), "src/lifeos-edificio/edificio.css"), "utf-8");

// Datos de prueba inventados, suficientes para que aparezcan listas, etiquetas y paginadores.
const TAREA = { id: "t-a", title: "T", status: "propuesta", area: "universidad", effort: "1h", progress: null, priority: "media", due: "2026-11-05", scheduled: null, path: "x.md", archived: false };
const ENTRADA = { ts: "2026-11-02T12:00:00+00:00", actor: "jefe", action: "publicar_plan", target: "x.md", result: "ok", reason: null, completed: true };
const veinte = <T,>(x: T): T[] => Array.from({ length: 20 }, () => x);

function tieneRegla(clase: string): boolean {
  return CSS.split(`.${clase}`).slice(1).some((resto) => !/^[\w-]/.test(resto));
}

describe("estilos de las ventanas", () => {
  it("cada clase lo-* en uso tiene una regla en edificio.css", async () => {
    installFakeApi({
      "GET /api/hoy": ok(briefing({ blocks: [block(), block()], adjustments: [{ day: "2026-11-02", at: "08:00", kind: "x", text: "mover" }] })),
      "GET /api/semana": ok(week()),
      "GET /api/tareas/proximas": ok(veinte(TAREA)),
      "GET /api/historial": ok({ entries: veinte(ENTRADA), malformed: 1, truncated: true }),
    });
    const ventanas = Object.values(crearVentanas(() => undefined));
    for (const w of ventanas) w.open();
    const pintada = (id: string) => ventanas.find((w) => w.id === id)?.el.querySelector(".lo-item");
    for (const id of ["tareas", "historial", "coach"]) await vi.waitFor(() => expect(pintada(id)).toBeTruthy());
    const usadas = new Set(ventanas.flatMap((w) => [w.el, ...w.el.querySelectorAll("*")]).flatMap((e) => [...e.classList]).filter((c) => c.startsWith("lo-")));
    expect([...usadas].filter((c) => !tieneRegla(c))).toEqual([]);
    expect(usadas.size).toBeGreaterThan(15);
  });

  it.each(["select", 'input[type="checkbox"]', "::file-selector-button", "option", "::placeholder", "summary", "legend", ":disabled", "scrollbar-color"])("hay estilo propio para %s", (selector) => {
    expect(CSS).toContain(selector);
  });
});
