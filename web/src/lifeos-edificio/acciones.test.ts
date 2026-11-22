// Contrato de las acciones del edificio: cada una llama a su ruta existente con el cuerpo que espera la API.
import { afterEach, describe, expect, it, vi } from "vitest";
import { installFakeApi, ok } from "../test/fakeApi";
import { acciones, RUTA } from "./acciones";
import rutas from "./rutas.json";

afterEach(() => vi.unstubAllGlobals());

function route(method: string, path: string, data: unknown = {}): Record<string, { body: unknown }> {
  return { [`${method} ${path}`]: ok(data) };
}

describe("acciones del edificio", () => {
  it("RUTA nombra exactamente las rutas del inventario", () => {
    expect(new Set(Object.values(RUTA))).toEqual(new Set(rutas));
  });

  it("el plan: semana vacía = la actual (null) y la IA se envía tal cual", async () => {
    const calls = installFakeApi({ ...route("POST", "/api/plan/propuesta"), ...route("POST", "/api/plan/aprobacion"), ...route("POST", "/api/plan/revision") });
    await acciones.proponer("  ", true);
    await acciones.aprobar("2026-W45");
    await acciones.replanificar("", false);
    const writes = calls.filter((c) => c.method === "POST");
    expect(writes.map((c) => [c.url, c.body])).toEqual([
      ["/api/plan/propuesta", { semana: null, ia: true }],
      ["/api/plan/aprobacion", { semana: "2026-W45" }],
      ["/api/plan/revision", { semana: null, ia: false }],
    ]);
  });

  it("coach: check-in, vista previa y cierre con las respuestas", async () => {
    const calls = installFakeApi({ ...route("POST", "/api/checkins"), ...route("POST", "/api/cierre/vista-previa"), ...route("POST", "/api/cierre") });
    await acciones.checkin({ texto: "voy", estado: "empezando", tarea: "t-aaaaaa", capturar: null, ia: false });
    await acciones.cierrePrevia([{ tarea: "t-aaaaaa", tipo: "hecha" }]);
    await acciones.cierre([{ tarea: "t-aaaaaa", tipo: "parcial", avance: "50" }]);
    const writes = calls.filter((c) => c.method === "POST");
    expect(writes.map((c) => [c.url, c.body])).toEqual([
      ["/api/checkins", { texto: "voy", estado: "empezando", tarea: "t-aaaaaa", capturar: null, ia: false }],
      ["/api/cierre/vista-previa", { respuestas: [{ tarea: "t-aaaaaa", tipo: "hecha" }] }],
      ["/api/cierre", { respuestas: [{ tarea: "t-aaaaaa", tipo: "parcial", avance: "50" }] }],
    ]);
  });

  it("agentes: el id va codificado en la URL y cada acción a su ruta", async () => {
    const calls = installFakeApi({
      ...route("POST", "/api/agentes/importacion"),
      ...route("POST", "/api/agentes/a%2Fb/prueba"),
      ...route("POST", "/api/agentes/a%2Fb/estado"),
      ...route("GET", "/api/agentes/a%2Fb/exportacion"),
      ...route("POST", "/api/agentes/a%2Fb/ejecucion"),
      ...route("POST", "/api/propuestas/aceptacion"),
      ...route("POST", "/api/propuestas/rechazo"),
    });
    await acciones.importar("finanzas", "id: finanzas\n");
    await acciones.probar("a/b", false);
    await acciones.estado("a/b", "activar");
    await acciones.exportar("a/b");
    await acciones.ejecutar("a/b", "2026-W45", false);
    await acciones.aceptar("ref-1");
    await acciones.rechazar("ref-2");
    const sent = calls.filter((c) => c.url !== "/api/session").map((c) => `${c.method} ${c.url} ${JSON.stringify(c.body)}`);
    expect(sent).toEqual([
      'POST /api/agentes/importacion {"nombre":"finanzas","contenido":"id: finanzas\\n"}',
      'POST /api/agentes/a%2Fb/prueba {"ia":false}',
      'POST /api/agentes/a%2Fb/estado {"accion":"activar"}',
      "GET /api/agentes/a%2Fb/exportacion null",
      'POST /api/agentes/a%2Fb/ejecucion {"semana":"2026-W45","ia":false}',
      'POST /api/propuestas/aceptacion {"ref":"ref-1"}',
      'POST /api/propuestas/rechazo {"ref":"ref-2"}',
    ]);
  });

  it("lecturas con sus parámetros (los vacíos no viajan)", async () => {
    const calls = installFakeApi({
      ...route("GET", "/api/tareas", []),
      ...route("GET", "/api/tareas/proximas", []),
      ...route("GET", "/api/historial", { entries: [] }),
      ...route("GET", "/api/indicadores"),
      ...route("GET", "/api/calendario/disponibilidad"),
      ...route("GET", "/api/semana"),
    });
    await acciones.tareas({ area: "universidad", estado: "", texto: "" });
    await acciones.proximas(14);
    await acciones.historial(200);
    await acciones.indicadores(28);
    await acciones.disponibilidad("");
    await acciones.semana("2026-W45");
    expect(calls.map((c) => c.url)).toEqual([
      "/api/tareas?area=universidad",
      "/api/tareas/proximas?dias=14",
      "/api/historial?limite=200",
      "/api/indicadores?dias=28",
      "/api/calendario/disponibilidad",
      "/api/semana?semana=2026-W45",
    ]);
  });

  it("captura y cierre de sesión", async () => {
    const calls = installFakeApi({ ...route("POST", "/api/capturas", { path: "00-Inbox/x.md" }), "POST /api/logout": { status: 204, body: null } });
    await expect(acciones.capturar("idea")).resolves.toEqual({ path: "00-Inbox/x.md" });
    await acciones.cerrarSesion();
    expect(calls.filter((c) => c.method === "POST").map((c) => c.url)).toEqual(["/api/capturas", "/api/logout"]);
  });
});
