// Ventanas de consulta, agentes, Jefe y barra con la API falsa (datos inventados, aislados en la prueba).
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agents, ImportedAgent, Indicators, ReviewItem } from "../api/types";
import type { UiContext } from "../habblaud/ui/context";
import { fail, installFakeApi, ok } from "../test/fakeApi";
import { Barra } from "./barra";
import type { LifeosStore } from "./store";
import { crearAgentes } from "./ventanas/agentes";
import { crearAyuda, crearCalendario, crearHistorial, crearIndicadores, crearPropuestas } from "./ventanas/consultas";
import { horas, momento } from "./ventanas/formato";
import { crearJefe } from "./ventanas/jefe";
import type { Ventana } from "./ventana";

afterEach(() => vi.unstubAllGlobals());

function boton(w: Ventana, texto: string, n = 0): HTMLButtonElement {
  const b = [...w.el.querySelectorAll("button")].filter((x) => x.textContent === texto)[n];
  if (b === undefined) throw new Error(`no hay botón «${texto}»`);
  return b;
}

const estado = (w: Ventana) => w.el.querySelector(".lo-status")?.textContent ?? "";

const SPEC: ImportedAgent = {
  id: "lectura", name: "Lectura", purpose: "Proponer lecturas", area: "universidad", profile: "ligero", state: "probado",
  current: 2, versions: [], history: [], effective: { reads: ["01-Universidad"], writes: [{ root: "Propuestas", mode: "crear", section: null }], tools: [] }, problems: ["ninguno grave"],
};
const AGENTES: Agents = {
  month: "2026-11", plan_owner: "jefe", forbidden_write: ["_sistema"], restricted_read: [], usage_malformed: 0, imported: [SPEC],
  agents: [{ id: "jefe", name: "Jefe", version: 1, purpose: "Planificar", status: "activo", profile: "ligero", provider: "nvidia", model: "m", contract_in: "", contract_out: "", reads: ["Semanal"], writes: [{ root: "Semanal", mode: "crear", section: "Plan" }], plan_writes: [], tools: [], metrics: [], usage: { agent: "jefe", ritual: "semana", calls: 2, attempts: 2, errors: 0, tokens_prompt: 1, tokens_completion: 1, tokens_unknown: 0, cost_estimated_usd: "0.01", cost_unknown: 0 } }],
};
const ROW = { agent: "jefe", ritual: "semana", calls: 2, attempts: 2, errors: 0, tokens_prompt: 1, tokens_completion: 1, tokens_unknown: 0, cost_estimated_usd: "0.01", cost_unknown: 0 };
const RUN = { agent_id: "lectura", week: "2026-W45", source: "heurística", dry_run: false, recorded_test: false, path: "Propuestas/x.md", items: [{ key: "k", kind: "prioridad" as const, priority: "alta", reason: "vence pronto", task_id: "t-aaaaaa", title: null, effort: null, due: null, status: "pendiente" }], alerts: ["una alerta"] };

describe("formatos", () => {
  it("horas y momentos legibles", () => {
    expect([horas(45), horas(120), horas(90)]).toEqual(["45 min", "2 h", "1 h 30 min"]);
    expect(momento("2026-11-02T12:30:00+00:00")).toBe("2026-11-02 12:30");
  });
});

describe("agentes y especialistas", () => {
  it("muestra el núcleo, el uso y cada especialista con sus acciones reales", async () => {
    const calls = installFakeApi({
      "GET /api/agentes": ok(AGENTES),
      "GET /api/uso": ok({ month: "2026-11", rows: [ROW], total: ROW, malformed: 0 }),
      "POST /api/agentes/lectura/prueba": ok({ ...RUN, dry_run: true }),
      "POST /api/agentes/lectura/ejecucion": ok(RUN),
      "POST /api/agentes/lectura/estado": ok({}),
      "GET /api/agentes/lectura/exportacion": ok({ version: 2, contenido: "id: lectura\n" }),
      "POST /api/agentes/importacion": ok({ ...SPEC, id: "nuevo", name: "Nuevo", state: "borrador" }),
    });
    URL.createObjectURL = vi.fn(() => "blob:x");
    URL.revokeObjectURL = vi.fn();
    Element.prototype.scrollIntoView = vi.fn(); // jsdom no lo implementa
    const w = crearAgentes(() => undefined);
    w.open();
    await vi.waitFor(() => expect(w.el.textContent).toContain("Lectura · probado · v2"));
    expect(w.el.textContent).toContain("Jefe · activo");
    expect(w.el.textContent).toContain("Total 2026-11: 2 llamada(s)");
    boton(w, "Probar").click();
    await vi.waitFor(() => expect(estado(w)).toContain("prueba registrada"));
    boton(w, "Ejecutar").click();
    await vi.waitFor(() => expect(estado(w)).toContain("propuestas escritas"));
    expect(w.el.textContent).toContain("Alerta: una alerta");
    boton(w, "Activar").click();
    await vi.waitFor(() => expect(estado(w)).toBe("Lectura: activar hecho."));
    boton(w, "Exportar").click();
    await vi.waitFor(() => expect(estado(w)).toBe("Lectura: versión 2 exportada."));
    const campos = w.el.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>(".lo-input");
    const nombre = [...campos].find((c) => c.getAttribute("placeholder")?.startsWith("nombre") === true);
    const contenido = [...campos].find((c) => c.getAttribute("placeholder")?.startsWith("Pega") === true);
    if (nombre === undefined || contenido === undefined) throw new Error("sin formulario de importación");
    nombre.value = "nuevo";
    contenido.value = "id: nuevo\n";
    boton(w, "Importar").click();
    await vi.waitFor(() => expect(estado(w)).toContain("Importado Nuevo (borrador)"));
    const posts = calls.filter((c) => c.method === "POST").map((c) => c.url);
    expect(posts).toEqual(["/api/agentes/lectura/prueba", "/api/agentes/lectura/ejecucion", "/api/agentes/lectura/estado", "/api/agentes/importacion"]);
    w.enfocar("lectura");
    expect(w.isOpen).toBe(true);
  });
});

describe("propuestas", () => {
  const P: ReviewItem = { key: "k", kind: "prioridad", priority: "alta", reason: "vence", task_id: "t-aaaaaa", title: null, effort: null, due: "2026-11-04", status: "pendiente", ref: "r1", area: "universidad", task_title: "Informe", current_priority: "media" };

  it("aceptar llama a su ruta y recarga; sin pendientes lo dice", async () => {
    let items = [P];
    const calls = installFakeApi({
      "GET /api/propuestas": () => ok({ items, skipped: ["roto.md"] }),
      "POST /api/propuestas/aceptacion": () => {
        items = [];
        return ok({});
      },
    });
    const w = crearPropuestas(() => undefined);
    w.open();
    await vi.waitFor(() => expect(w.el.textContent).toContain("Prioridad de Informe: media → alta"));
    expect(w.el.textContent).toContain("roto.md");
    boton(w, "Aceptar").click();
    await vi.waitFor(() => expect(w.el.textContent).toContain("No hay propuestas pendientes."));
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ ref: "r1" });
  });
});

describe("historial, indicadores y calendario", () => {
  it("el historial muestra lo más reciente primero", async () => {
    installFakeApi({ "GET /api/historial": ok({ entries: [{ ts: "2026-11-02T12:00:00+00:00", actor: "jefe", action: "plan_proponer", target: "Semanal/2026-W45.md", result: "ok", reason: null, completed: true }, { ts: "2026-11-02T13:00:00+00:00", actor: "coach", action: "anexar_checkin", target: "Diario/2026-11-02.md", result: "rechazado", reason: "sin permiso", completed: true }], malformed: 1, truncated: true }) });
    const w = crearHistorial();
    w.open();
    await vi.waitFor(() => expect(w.el.querySelectorAll(".lo-list li")).toHaveLength(2));
    expect(w.el.querySelector(".lo-list li")?.textContent).toContain("anexar checkin · Diario/2026-11-02.md · rechazado (sin permiso)");
    expect(w.el.textContent).toContain("Se muestran las 200 más recientes.");
  });

  it("los indicadores dicen «sin datos» en vez de inventar un 0", async () => {
    const ind: Indicators = {
      start: "2026-10-06", end: "2026-11-02", streak: 0, focus_max_minutes: 240,
      deliveries: { due: 0, completed: 0, forgotten: 0, percent: null }, adherence: { closed_days: 0, answered: 0, percent: null },
      usage: { active_days: 1, days_per_week: 0.3 }, acceptance: { proposed: 1, approved: 1, minor_changes: 1, percent: 100 },
      costs: [{ agent: "jefe", ritual: "semana", calls: 1, errors: 0, latency_median_ms: 10, latency_p95_ms: 20, cost_estimated_usd: "0", cost_unknown: 0 }],
      notifications: { sent: 0, failed: 0, skipped: 0, days_with_notifications: 0, max_per_day: 0, per_active_day: null, by_kind: [] },
      not_measured: [{ name: "Foco", reason: "falta el registro" }], quality: { malformed_log_lines: 0, unreadable_notes: 0, invalid_task_lines: 0, unreadable_closings: 0 },
    };
    installFakeApi({ "GET /api/indicadores": ok(ind) });
    const w = crearIndicadores();
    w.open();
    await vi.waitFor(() => expect(w.el.textContent).toContain("Entregas: 0 de 0 (sin datos)"));
    expect(w.el.textContent).toContain("Aceptación de planes: 100 %");
    expect(w.el.textContent).toContain("Foco: falta el registro");
  });

  it("el calendario es de solo lectura y consulta la disponibilidad", async () => {
    installFakeApi({
      "GET /api/calendario": ok({ configured: false, grants: [{ grant: "lectura", scope: "x", connected: false, obtained: null, problem: null }], can_read: false, problem: "falta client_secret" }),
      "GET /api/calendario/disponibilidad": ok({ week: "2026-W45", zone: "America/Lima", plan_path: null, plan_status: null, busy: [], clashes: [] }),
    });
    const w = crearCalendario();
    w.open();
    await vi.waitFor(() => expect(w.el.textContent).toContain("Google Calendar no está configurado."));
    boton(w, "Consultar").click();
    await vi.waitFor(() => expect(w.el.textContent).toContain("Sin choques."));
    expect(w.el.textContent).toContain("se hacen en la CLI");
  });

  it("la ayuda no llama a la API", () => {
    const calls = installFakeApi({});
    crearAyuda().open();
    expect(calls).toEqual([]);
  });
});

describe("oficina del Jefe", () => {
  it("proponer muestra el plan, lo que quedó sin hueco y recarga la semana", async () => {
    const refrescar = vi.fn();
    const semana = { week: "2026-W45", zone: "America/Lima", path: "Semanal/2026-W45.md", status: "propuesto", origin: "heurística", revision: 1, versions: 1, generated: null, justification: "Por carga.", days: [], blocks: [], load: [{ day: "2026-11-02", budget_minutes: 240, planned_minutes: 300, fixed_minutes: 120, protected_minutes: 0, free_minutes: 0, over_budget: true }] };
    installFakeApi({
      "GET /api/semana": ok(semana),
      "POST /api/plan/propuesta": ok({ accepted: true, status: "propuesto", path: "Semanal/2026-W45.md", proposal: { week: "2026-W45", origin: "heurística", work: [], unplanned: [{ task_id: null, title: "Leer", minutes: 60, reason: "sin hueco", suggestion: "muévela" }], alerts: ["mucha carga"], justification: "" }, conflicts: [] }),
    });
    const w = crearJefe(refrescar);
    boton(w, "Proponer la semana").click();
    await vi.waitFor(() => expect(estado(w)).toBe("Plan propuesto. Revísalo y apruébalo."));
    expect(w.el.textContent).toContain("Leer (1 h): sin hueco. muévela");
    expect(w.el.textContent).toContain("mucha carga");
    await vi.waitFor(() => expect(w.el.textContent).toContain("¡sobre el límite!"));
    expect(refrescar).toHaveBeenCalled();
  });

  it("un fallo de red se muestra en la ventana", async () => {
    installFakeApi({ "GET /api/semana": fail(500, "interno", "fallo del servidor") });
    const w = crearJefe(() => undefined);
    w.open();
    await vi.waitFor(() => expect(estado(w)).toBe("fallo del servidor"));
  });
});

describe("barra", () => {
  it("dice la verdad sobre la conexión y enseña el error real", () => {
    const store = { connection: "closed", lastError: "sin sesión" } as unknown as LifeosStore;
    const ctx = { camera: vi.fn(), togglePanel: vi.fn(), openHelp: vi.fn() } as unknown as UiContext;
    const click = vi.fn();
    const barra = new Barra(ctx, store, [{ id: "x", label: "X", icon: "<svg></svg>", onClick: click, ruta: "POST /api/logout" }]);
    barra.render();
    const pill = barra.el.querySelector<HTMLElement>(".ui-pill");
    expect(pill?.textContent).toBe("Sin conexión");
    expect(pill?.title).toBe("sin sesión");
    barra.el.querySelector<HTMLButtonElement>('[data-boton="x"]')?.click();
    expect(click).toHaveBeenCalled();
    expect(barra.el.querySelector('[data-boton="x"]')?.getAttribute("data-ruta")).toBe("POST /api/logout");
  });
});
