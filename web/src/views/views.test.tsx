import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resetSession } from "../api/client";
import type { Agents, History, Task } from "../api/types";
import { App } from "../App";
import { block, briefing, fail, installFakeApi, ok, week, XSS } from "../test/fakeApi";
import { AgentsView } from "./AgentsView";
import { HistoryView } from "./HistoryView";
import { TasksView } from "./TasksView";
import { TodayView } from "./TodayView";
import { WeekView } from "./WeekView";

afterEach(() => {
  vi.unstubAllGlobals();
  resetSession();
  window.location.hash = "";
});

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "t-aaaaaa",
    title: "Informe de redes",
    status: "propuesta",
    area: "universidad",
    effort: "3h",
    progress: null,
    priority: "media",
    due: "2026-11-04",
    scheduled: null,
    path: "01-Universidad/redes.md",
    archived: false,
    ...overrides,
  };
}

describe("App y sesión", () => {
  it("sin sesión pide abrir la URL de la terminal", async () => {
    installFakeApi({ "GET /api/session": fail(401, "sin_sesion", "abre la URL") });
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("URL de un solo uso");
  });

  it("con sesión muestra las pestañas y se navega con el teclado", async () => {
    installFakeApi({ "GET /api/hoy": ok(briefing()), "GET /api/semana": ok(week()) });
    const user = userEvent.setup();
    render(<App />);
    const today = await screen.findByRole("tab", { name: "Hoy" });
    expect(today).toHaveAttribute("aria-selected", "true");
    today.focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: "Semana" })).toHaveAttribute("aria-selected", "true");
    expect(await screen.findByRole("heading", { name: /Semana 2026-W45/ })).toBeInTheDocument();
  });

  it("cerrar sesión vuelve a la pantalla de acceso", async () => {
    const calls = installFakeApi({ "GET /api/hoy": ok(briefing()), "POST /api/logout": { status: 204, body: null } });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Cerrar sesión" }));
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.url === "/api/logout")).toBe(true);
  });
});

describe("Hoy", () => {
  it("muestra el bloque actual y el tiempo protegido", async () => {
    const gym = block({ kind: "protegido", title: "Gym", task_id: null, span: "18:00–19:30" });
    installFakeApi({ "GET /api/hoy": ok(briefing({ resting: gym, protected: [gym], checkins: 2 })) });
    render(<TodayView />);
    expect(await screen.findByText(/Descansa sin culpa/)).toBeInTheDocument();
    expect(screen.getByText(/Check-ins: 2/)).toBeInTheDocument();
  });

  it("muestra el error del caso de uso cuando no hay plan", async () => {
    installFakeApi({ "GET /api/hoy": fail(409, "rechazado", "no hay plan para 2026-11-02: propón y aprueba la semana") });
    render(<TodayView />);
    expect(await screen.findByRole("alert")).toHaveTextContent("propón y aprueba la semana");
  });

  it("el check-in llama al caso de uso y muestra la respuesta del Coach", async () => {
    const calls = installFakeApi({
      "GET /api/hoy": ok(briefing()),
      "POST /api/checkins": {
        status: 201,
        body: { ok: true, data: { kind: "micro_paso", message: "Empieza por algo pequeño.", micro_step: { action: "Abre el archivo", minutes: 5 }, adjustment: null, path: "Diario/2026-11-02.md", capture: null, ai_fallback: false, readable: true } },
      },
    });
    const user = userEvent.setup();
    render(<TodayView />);
    await screen.findByRole("heading", { name: /Hoy 2026-11-02/ });
    await user.selectOptions(screen.getByLabelText("¿Cómo vas?"), "bloqueado");
    await user.selectOptions(screen.getByLabelText("Tarea del bloque"), "t-aaaaaa");
    await user.type(screen.getByLabelText("Mensaje"), "No sé por dónde empezar");
    await user.click(screen.getByRole("button", { name: "Enviar check-in" }));
    const log = await screen.findByRole("log", { name: "Conversación con el Coach" });
    expect(await within(log).findByText("Micro-paso (5 min): Abre el archivo")).toBeInTheDocument();
    const sent = calls.find((c) => c.method === "POST" && c.url === "/api/checkins");
    expect(sent?.body).toEqual({ texto: "No sé por dónde empezar", estado: "bloqueado", tarea: "t-aaaaaa", capturar: null, ia: false });
  });

  it("el cierre exige ver la vista previa antes de confirmar", async () => {
    const preview = { day: "2026-11-02", path: "Diario/2026-11-02.md", changes: [{ task_id: "t-aaaaaa", title: "Informe de redes", kind: "parcial", progress_before: "25%", progress_after: "60%", remaining_before: "3h", remaining_after: null }], unanswered: [], missed: [], closed: false };
    const calls = installFakeApi({
      "GET /api/hoy": ok(briefing()),
      "POST /api/cierre/vista-previa": ok(preview),
      "POST /api/cierre": ok({ ...preview, written: ["01-Universidad/redes.md", "Diario/2026-11-02.md"], adjustments: [] }),
    });
    const user = userEvent.setup();
    render(<TodayView />);
    const confirm = await screen.findByRole("button", { name: "Confirmar cierre" });
    expect(confirm).toBeDisabled();
    await user.click(screen.getByRole("radio", { name: "Parcial" }));
    await user.type(screen.getByLabelText("Avance (%)"), "60");
    await user.click(screen.getByRole("button", { name: "Ver vista previa" }));
    expect(await screen.findByText("Informe de redes → avance 25% → 60%")).toBeInTheDocument();
    await user.click(confirm);
    expect(await screen.findByText(/Cierre registrado en 01-Universidad\/redes.md/)).toBeInTheDocument();
    const confirmed = calls.find((c) => c.url === "/api/cierre");
    expect(confirmed?.body).toEqual({ respuestas: [{ tarea: "t-aaaaaa", tipo: "parcial", avance: "60" }] });
  });
});

describe("Semana y chat con el Jefe", () => {
  it("muestra la carga por día y avisa del exceso", async () => {
    installFakeApi({ "GET /api/semana": ok(week()) });
    render(<WeekView />);
    const monday = await screen.findByRole("article", { name: "Lunes 2026-11-02" });
    expect(within(monday).getByText(/por encima del límite/)).toBeInTheDocument();
    expect(within(monday).getByText(/Fijo 2 h/)).toBeInTheDocument();
    expect(screen.getAllByRole("meter")).toHaveLength(7);
  });

  it("un plan rechazado muestra sus conflictos en el chat", async () => {
    const rejectedPlan = { accepted: false, status: null, path: null, proposal: { week: "2026-W45", origin: "heuristica", work: [], unplanned: [], alerts: [], justification: "" }, conflicts: [{ code: "solapamiento", message: "choca con la clase", day: "2026-11-02" }] };
    const calls = installFakeApi({
      "GET /api/semana": ok(week()),
      "POST /api/plan/aprobacion": fail(422, "plan_rechazado", "1 conflicto(s)", rejectedPlan),
    });
    const user = userEvent.setup();
    render(<WeekView />);
    await user.click(await screen.findByRole("button", { name: "Aprobar el plan propuesto" }));
    const log = await screen.findByRole("log", { name: "Conversación con el Jefe" });
    expect(await within(log).findByText("[solapamiento] choca con la clase")).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/plan/aprobacion")?.body).toEqual({ semana: null });
  });

  it("proponer con IA envía la opción al caso de uso", async () => {
    const calls = installFakeApi({
      "GET /api/semana": ok(week({ status: null, blocks: [] })),
      "POST /api/plan/propuesta": fail(503, "configuracion", "falta la variable NVIDIA_API_KEY"),
    });
    const user = userEvent.setup();
    render(<WeekView />);
    await user.click(await screen.findByRole("checkbox", { name: "Proponer con IA" }));
    await user.click(screen.getByRole("button", { name: "Proponer la semana" }));
    expect(await screen.findByText("falta la variable NVIDIA_API_KEY")).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/plan/propuesta")?.body).toEqual({ semana: null, ia: true });
  });
});

describe("contenido de notas como texto (XSS)", () => {
  it("un título malicioso se muestra literal y no crea elementos", async () => {
    const evil = block({ title: XSS });
    installFakeApi({
      "GET /api/hoy": ok(briefing({ current: evil, work: [evil], blocks: [evil] })),
    });
    const { container } = render(<TodayView />);
    expect((await screen.findAllByText(XSS)).length).toBeGreaterThan(0);
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
  });

  it("tareas, historial y agentes también lo muestran como texto", async () => {
    const history: History = { entries: [{ ts: "2026-11-02T15:00:00+00:00", actor: "coach", action: "anexar_checkin", target: XSS, result: "rechazado", reason: "<script>alert(1)</script>", completed: true }], malformed: 0, truncated: false };
    const agents: Agents = { month: "2026-11", plan_owner: "jefe", forbidden_write: ["_sistema/"], restricted_read: ["05-Salud/"], usage_malformed: 0, agents: [{ id: "coach", name: XSS, version: 1, purpose: "<b>x</b>", status: "activo", profile: "ligero", provider: "nvidia", model: "m", contract_in: "checkin_v1", contract_out: "coach_v1", reads: ["Diario/"], writes: [], plan_writes: [], tools: ["anexar_checkin"], metrics: [], usage: null }] };
    installFakeApi({
      "GET /api/tareas/proximas": ok([task({ title: XSS })]),
      "GET /api/tareas": ok([task({ title: XSS })]),
      "GET /api/historial": ok(history),
      "GET /api/agentes": ok(agents),
    });
    const tasks = render(<TasksView />);
    expect((await screen.findAllByText(XSS)).length).toBe(2);
    tasks.unmount();
    const historyView = render(<HistoryView />);
    expect(await screen.findByText(XSS)).toBeInTheDocument();
    historyView.unmount();
    const { container } = render(<AgentsView />);
    expect(await screen.findByText("<b>x</b>")).toBeInTheDocument();
    expect(container.querySelector("img, script, b")).toBeNull();
  });
});

describe("Tareas", () => {
  it("la captura rápida llama al caso de uso y recarga las listas", async () => {
    const calls = installFakeApi({
      "GET /api/tareas/proximas": ok([]),
      "GET /api/tareas": ok([task()]),
      "POST /api/capturas": { status: 201, body: { ok: true, data: { path: "00-Inbox/2026-11-02-idea.md" } } },
    });
    const user = userEvent.setup();
    render(<TasksView />);
    await user.type(await screen.findByLabelText(/Idea, tarea o recordatorio/), "Comprar cuaderno");
    await user.click(screen.getByRole("button", { name: "Guardar" }));
    expect(await screen.findByRole("status")).toHaveTextContent("00-Inbox/2026-11-02-idea.md");
    await waitFor(() => expect(calls.filter((c) => c.url === "/api/tareas").length).toBeGreaterThan(1));
  });

  it("los filtros se envían a la API", async () => {
    const calls = installFakeApi({ "GET /api/tareas/proximas": ok([]), "GET /api/tareas": ok([]) });
    const user = userEvent.setup();
    render(<TasksView />);
    await user.selectOptions(await screen.findByLabelText("Área"), "universidad");
    await waitFor(() => expect(calls.some((c) => c.url === "/api/tareas?area=universidad")).toBe(true));
  });
});

describe("estados menos frecuentes", () => {
  it("Hoy avisa de una nota fuera de formato y de propuestas al Jefe", async () => {
    installFakeApi({
      "GET /api/hoy": ok(
        briefing({
          readable: false,
          approved: false,
          current: null,
          rejected_adjustments: 1,
          adjustments: [{ day: "2026-11-02", at: "08:30", kind: "reducir carga hoy", text: "ajuste: reducir carga hoy" }],
        }),
      ),
    });
    render(<TodayView />);
    expect(await screen.findByText(/plan sin aprobar/)).toBeInTheDocument();
    expect(screen.getByText(/no sigue el formato del plan/)).toBeInTheDocument();
    expect(screen.getByText(/Propuestas para el Jefe: 1/)).toBeInTheDocument();
    expect(screen.getByText(/no siguen el formato y el Jefe no las verá/)).toBeInTheDocument();
    expect(screen.getByText("Sin bloque")).toBeInTheDocument();
  });

  it("el cierre muestra el error del caso de uso y admite hecha y no", async () => {
    const calls = installFakeApi({
      "GET /api/hoy": ok(briefing({ work: [block(), block({ task_id: "t-bbbbbb", title: "Leer", start: "2026-11-02T10:00:00-05:00" })] })),
      "POST /api/cierre/vista-previa": fail(422, "entrada_invalida", "cierre: respuesta repetida"),
    });
    const user = userEvent.setup();
    render(<TodayView />);
    const [first, second] = await screen.findAllByRole("group", { name: /t-/ });
    await user.click(within(first as HTMLElement).getByRole("radio", { name: "Hecha" }));
    await user.click(within(second as HTMLElement).getByRole("radio", { name: "No la hice" }));
    await user.click(screen.getByRole("button", { name: "Ver vista previa" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("respuesta repetida");
    expect(calls.find((c) => c.url === "/api/cierre/vista-previa")?.body).toEqual({
      respuestas: [{ tarea: "t-aaaaaa", tipo: "hecha" }, { tarea: "t-bbbbbb", tipo: "no" }],
    });
  });

  it("un check-in que falla se muestra como error en el chat", async () => {
    installFakeApi({ "GET /api/hoy": ok(briefing()), "POST /api/checkins": fail(429, "limite_tasa", "demasiadas peticiones con IA") });
    const user = userEvent.setup();
    render(<TodayView />);
    await user.click(await screen.findByRole("checkbox", { name: /Pedir el mensaje al modelo/ }));
    await user.type(screen.getByLabelText(/Captura rápida/), "Idea");
    await user.click(screen.getByRole("button", { name: "Enviar check-in" }));
    expect(await screen.findByText("demasiadas peticiones con IA")).toBeInTheDocument();
  });

  it("Semana muestra la revisión y no consulta una semana mal escrita", async () => {
    const calls = installFakeApi({ "GET /api/semana": ok(week({ revision: 1, justification: "Reubico el bloque perdido." })) });
    const user = userEvent.setup();
    render(<WeekView />);
    expect(await screen.findByRole("heading", { name: /revisión 1/ })).toBeInTheDocument();
    expect(screen.getByText("Reubico el bloque perdido.")).toBeInTheDocument();
    await user.type(screen.getByLabelText(/Semana \(AAAA-Wnn/), "2026-4");
    expect(screen.getByLabelText(/Semana \(AAAA-Wnn/)).toHaveAttribute("aria-invalid", "true");
    expect(calls.every((c) => !c.url.includes("2026-4"))).toBe(true);
  });

  it("una propuesta aceptada recarga la semana", async () => {
    const accepted = { accepted: true, status: "propuesto", path: "Semanal/2026-W45.md", proposal: { week: "2026-W45", origin: "heuristica", work: [block()], unplanned: [], alerts: [], justification: "" }, conflicts: [] };
    const calls = installFakeApi({ "GET /api/semana": ok(week()), "POST /api/plan/revision": ok(accepted) });
    const user = userEvent.setup();
    render(<WeekView />);
    await user.click(await screen.findByRole("button", { name: "Replanificar desde ahora" }));
    expect(await screen.findByText(/Plan propuesto para 2026-W45/)).toBeInTheDocument();
    await waitFor(() => expect(calls.filter((c) => c.url.startsWith("/api/semana")).length).toBe(2));
  });

  it("el historial marca intentos sin resultado y líneas ilegibles", async () => {
    const history: History = { entries: [{ ts: "no-es-fecha", actor: "jefe", action: "publicar_plan", target: "Semanal/2026-W45.md", result: "intento", reason: null, completed: false }], malformed: 2, truncated: true };
    installFakeApi({ "GET /api/historial": ok(history) });
    render(<HistoryView />);
    expect(await screen.findByText("sin resultado")).toBeInTheDocument();
    expect(screen.getByText("no-es-fecha")).toBeInTheDocument();
    expect(screen.getByText(/2 línea\(s\) de la auditoría/)).toBeInTheDocument();
    expect(screen.getByText(/los 200 más recientes/)).toBeInTheDocument();
  });

  it("el historial vacío lo dice", async () => {
    installFakeApi({ "GET /api/historial": ok({ entries: [], malformed: 0, truncated: false }) });
    render(<HistoryView />);
    expect(await screen.findByText("Sin cambios registrados.")).toBeInTheDocument();
  });

  it("los agentes muestran su uso y quién publica el plan", async () => {
    const usage = { agent: "jefe", ritual: "todos", calls: 3, attempts: 4, errors: 1, tokens_prompt: 100, tokens_completion: 50, tokens_unknown: 0, cost_estimated_usd: "0.0100", cost_unknown: 1 };
    const agents: Agents = { month: "2026-11", plan_owner: "jefe", forbidden_write: [], restricted_read: [], usage_malformed: 0, agents: [{ id: "jefe", name: "Jefe", version: 2, purpose: "Planificar", status: "activo", profile: "grande", provider: "nvidia", model: "m", contract_in: "plan_v1", contract_out: "plan_v1", reads: [], writes: [{ root: "00-Inbox/", mode: "crear", section: null }], plan_writes: [{ root: "Semanal/", mode: "plan", section: null }], tools: ["publicar_plan"], metrics: [], usage }] };
    installFakeApi({ "GET /api/agentes": ok(agents) });
    render(<AgentsView />);
    expect(await screen.findByText(/3 llamadas, 4 intentos, 1 errores/)).toBeInTheDocument();
    expect(screen.getByText(/\+ 1 desconocido/)).toBeInTheDocument();
    expect(screen.getByText("Semanal/ (plan)")).toBeInTheDocument();
  });

  it("si el servidor no responde lo dice", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("Failed to fetch"); }));
    render(<App />);
    expect(await screen.findByRole("alert")).toHaveTextContent("¿Está en marcha lifeos-web?");
  });
});
