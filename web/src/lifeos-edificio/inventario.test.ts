// «Todas y cada una de las acciones»: cada ruta de la API (rutas.json, sincronizado con el servidor por
// tests/web/test_edificio_rutas.py) tiene un botón o una ventana en el edificio, y ningún botón queda sin destino.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agents, ImportedAgent, ReviewItem } from "../api/types";
import { installFakeApi, ok } from "../test/fakeApi";
import { botonesBarra, crearVentanas, oficina } from "./interfaz";
import { COACH_ROOM, JEFE_ROOM, OWNER_ID, OWNER_ROOM } from "./adapter";
import rutas from "./rutas.json";

/** La sesión no es una acción: el cliente la pide sola para obtener el token CSRF antes de cada escritura. */
const IMPLICITAS = new Set(["GET /api/session"]);

afterEach(() => vi.unstubAllGlobals());

// Datos de prueba inventados: un especialista y una propuesta, para que existan sus botones.
const ESPECIALISTA: ImportedAgent = {
  id: "lectura", name: "Lectura", purpose: "Proponer lecturas", area: "universidad", profile: "ligero", state: "probado",
  current: 1, versions: [], history: [], effective: null, problems: [],
};
const AGENTES: Agents = { month: "2026-11", agents: [], plan_owner: "jefe", forbidden_write: [], restricted_read: [], usage_malformed: 0, imported: [ESPECIALISTA] };
const PROPUESTA: ReviewItem = {
  key: "k", kind: "nueva_tarea", priority: "media", reason: "porque sí", task_id: null, title: "Leer cap. 3", effort: "1h", due: null,
  status: "pendiente", ref: "lectura:2026-W45:k", area: "universidad", task_title: null, current_priority: null,
};
const USO = { month: "2026-11", rows: [], total: { agent: "", ritual: "", calls: 0, attempts: 0, errors: 0, tokens_prompt: 0, tokens_completion: 0, tokens_unknown: 0, cost_estimated_usd: "0", cost_unknown: 0 }, malformed: 0 };

async function conDatos() {
  installFakeApi({ "GET /api/agentes": ok(AGENTES), "GET /api/uso": ok(USO), "GET /api/propuestas": ok({ items: [PROPUESTA], skipped: [] }) });
  const m = montar();
  m.ventanas.agentes.open();
  m.ventanas.propuestas.open();
  await vi.waitFor(() => expect(m.root.querySelector('[data-ruta="POST /api/propuestas/rechazo"]')).not.toBeNull());
  await vi.waitFor(() => expect(m.root.querySelector('[data-ruta="POST /api/agentes/{agent_id}/ejecucion"]')).not.toBeNull());
  return m;
}

function montar() {
  const ventanas = crearVentanas(() => undefined);
  const root = document.createElement("div");
  for (const w of Object.values(ventanas)) root.append(w.el);
  return { ventanas, root };
}

describe("inventario del edificio", () => {
  it("cada ruta de la API tiene un botón o una ventana", async () => {
    const { ventanas, root } = await conDatos();
    const cubiertas = new Set<string>();
    root.querySelectorAll<HTMLElement>("[data-ruta]").forEach((el) => cubiertas.add(el.dataset.ruta ?? ""));
    root.querySelectorAll<HTMLElement>("[data-rutas]").forEach((el) => (el.dataset.rutas ?? "").split("|").forEach((r) => cubiertas.add(r)));
    for (const b of botonesBarra(ventanas, async () => undefined)) if (b.ruta !== undefined) cubiertas.add(b.ruta);
    const faltan = rutas.filter((r) => !cubiertas.has(r) && !IMPLICITAS.has(r));
    expect(faltan).toEqual([]);
  });

  it("ningún botón de las ventanas apunta a una ruta que no existe", async () => {
    const { root } = await conDatos();
    const desconocidas = [...root.querySelectorAll<HTMLElement>("[data-ruta]")].map((el) => el.dataset.ruta ?? "").filter((r) => !rutas.includes(r));
    expect(desconocidas).toEqual([]);
  });

  it("cada botón de la barra abre una ventana real o hace una acción real", () => {
    const { ventanas } = montar();
    const botones = botonesBarra(ventanas, async () => undefined);
    const conVentana = botones.filter((b) => b.ruta === undefined);
    for (const b of conVentana) {
      b.onClick(document.createElement("button"));
      const w = Object.values(ventanas).find((v) => v.id === b.id);
      expect(w?.isOpen, `«${b.label}» abre su ventana`).toBe(true);
    }
    expect(botones.find((b) => b.id === "salir")?.ruta).toBe("POST /api/logout");
  });

  it("clic en una sala o en un agente abre su oficina", () => {
    const { ventanas } = montar();
    const roomOf = (id: string) => ({ jefe: JEFE_ROOM, coach: COACH_ROOM, [OWNER_ID]: OWNER_ROOM, lectura: "especialista:lectura" })[id];
    oficina(ventanas, { type: "agent", id: "jefe" }, roomOf)?.();
    oficina(ventanas, { type: "room", id: COACH_ROOM }, roomOf)?.();
    oficina(ventanas, { type: "agent", id: OWNER_ID }, roomOf)?.();
    oficina(ventanas, { type: "agent", id: "lectura" }, roomOf)?.();
    expect([ventanas.jefe, ventanas.coach, ventanas.tareas, ventanas.agentes].map((w) => w.isOpen)).toEqual([true, true, true, true]);
    expect(oficina(ventanas, null, roomOf)).toBeNull();
  });
});
