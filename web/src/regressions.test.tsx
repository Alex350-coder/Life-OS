// Regresiones de la revisión de la Fase 5 (react-reviewer, typescript-reviewer, a11y-architect).
// Cada prueba reproduce un hallazgo antes de corregirlo.
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, get, post, resetSession } from "./api/client";
import { useApi } from "./api/useApi";
import { App } from "./App";
import { block, briefing, fail, installFakeApi, ok, week } from "./test/fakeApi";
import { TasksView } from "./views/TasksView";
import { TodayView } from "./views/TodayView";
import { WeekView } from "./views/WeekView";

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  resetSession();
  window.location.hash = "";
});

describe("sesión (HIGH)", () => {
  it("un 403 csrf renueva el token y repite la escritura una vez", async () => {
    let tokens = 0;
    const calls = installFakeApi({
      "GET /api/session": () => ({ body: { ok: true, data: { csrf_token: `csrf-${++tokens}` } } }),
      "POST /api/capturas": (request) =>
        request.headers["X-CSRF-Token"] === "csrf-2"
          ? { status: 201, body: { ok: true, data: { path: "00-Inbox/x.md" } } }
          : fail(403, "csrf", "falta o no coincide el token CSRF"),
    });
    await expect(post("/api/capturas", { texto: "a" })).resolves.toEqual({ path: "00-Inbox/x.md" });
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(2);
  });

  it("un 401 en cualquier vista devuelve la App a la pantalla de acceso", async () => {
    installFakeApi({ "GET /api/hoy": fail(401, "sin_sesion", "abre la URL") });
    render(<App />);
    expect(await screen.findByText(/No hay sesión/)).toBeInTheDocument();
  });

  it("una respuesta null es un ApiError, no un TypeError", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("null", { status: 200 })));
    await expect(get("/api/hoy")).rejects.toBeInstanceOf(ApiError);
  });
});

describe("datos desactualizados (HIGH)", () => {
  it("al cambiar de clave no se muestran los datos de la anterior", async () => {
    let resolveSecond: (value: string) => void = () => undefined;
    const loads = [Promise.resolve("semana 45"), new Promise<string>((resolve) => (resolveSecond = resolve))];
    function Probe({ id }: { id: string }) {
      const state = useApi(() => loads.shift() ?? Promise.resolve("?"), id);
      return <p>{state.loading ? "cargando" : state.data}</p>;
    }
    const { rerender } = render(<Probe id="45" />);
    expect(await screen.findByText("semana 45")).toBeInTheDocument();
    rerender(<Probe id="46" />);
    expect(screen.getByText("cargando")).toBeInTheDocument();
    expect(screen.queryByText("semana 45")).toBeNull();
    await act(async () => resolveSecond("semana 46"));
    expect(screen.getByText("semana 46")).toBeInTheDocument();
  });
});

describe("Semana", () => {
  it("con una semana mal escrita el Jefe no actúa y se explica el formato", async () => {
    installFakeApi({ "GET /api/semana": ok(week()) });
    const user = userEvent.setup();
    render(<WeekView />);
    const input = await screen.findByLabelText(/Semana \(AAAA-Wnn/);
    await user.type(input, "2026-4");
    expect(input).toHaveAccessibleDescription(/Formato AAAA-Wnn/);
    expect(screen.getByRole("button", { name: "Proponer la semana" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Aprobar el plan propuesto" })).toBeDisabled();
  });

  it("el medidor de carga dice el porcentaje real aunque pase del límite", async () => {
    installFakeApi({ "GET /api/semana": ok(week()) });
    render(<WeekView />);
    const meters = await screen.findAllByRole("meter");
    expect(meters[0]).toHaveAccessibleName(/125 %/);
  });
});

describe("Hoy y cierre", () => {
  it("las respuestas de tareas que ya no están hoy no se envían", async () => {
    const other = block({ task_id: "t-bbbbbb", title: "Leer", start: "2026-11-02T10:00:00-05:00" });
    let day = briefing({ work: [block(), other] });
    const calls = installFakeApi({
      "GET /api/hoy": () => ok(day),
      "POST /api/checkins": { status: 201, body: { ok: true, data: { kind: "registro", message: "Anotado.", micro_step: null, adjustment: null, path: "Diario/2026-11-02.md", capture: null, ai_fallback: false, readable: true } } },
      "POST /api/cierre/vista-previa": ok({ day: "2026-11-02", path: "Diario/2026-11-02.md", changes: [], unanswered: [], missed: [], closed: false }),
    });
    const user = userEvent.setup();
    render(<TodayView />);
    const groups = await screen.findAllByRole("group", { name: /t-/ });
    await user.click(within(groups[1] as HTMLElement).getByRole("radio", { name: "Hecha" }));
    day = briefing({ work: [block()] });
    await user.click(screen.getByRole("button", { name: "Enviar check-in" })); // refresca Hoy
    await waitFor(() => expect(screen.getAllByRole("group", { name: /t-/ })).toHaveLength(1));
    await user.click(screen.getByRole("button", { name: "Ver vista previa" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/cierre/vista-previa")).toBe(true));
    expect(calls.find((c) => c.url === "/api/cierre/vista-previa")?.body).toEqual({ respuestas: [] });
  });

  it("tras confirmar el cierre el foco va al mensaje de resultado", async () => {
    const preview = { day: "2026-11-02", path: "Diario/2026-11-02.md", changes: [], unanswered: [], missed: [], closed: false };
    installFakeApi({
      "GET /api/hoy": ok(briefing()),
      "POST /api/cierre/vista-previa": ok(preview),
      "POST /api/cierre": ok({ ...preview, written: ["Diario/2026-11-02.md"], adjustments: [] }),
    });
    const user = userEvent.setup();
    render(<TodayView />);
    await user.click(await screen.findByRole("button", { name: "Ver vista previa" }));
    await user.click(await screen.findByRole("button", { name: "Confirmar cierre" }));
    await waitFor(() => expect(screen.getByText(/Cierre registrado/)).toHaveFocus());
  });

  it("los campos del check-in se bloquean mientras se envía", async () => {
    let release: () => void = () => undefined;
    installFakeApi({ "GET /api/hoy": ok(briefing()) });
    const pending = new Promise<Response>((resolve) => {
      release = () => resolve(new Response(JSON.stringify({ ok: true, data: { kind: "registro", message: "Anotado.", micro_step: null, adjustment: null, path: "Diario/x.md", capture: null, ai_fallback: false, readable: true } }), { status: 201 }));
    });
    const original = globalThis.fetch;
    vi.stubGlobal("fetch", vi.fn((input: string, init?: RequestInit) => (input === "/api/checkins" ? pending : original(input, init))));
    const user = userEvent.setup();
    render(<TodayView />);
    await user.click(await screen.findByRole("button", { name: "Enviar check-in" }));
    expect(screen.getByLabelText("Mensaje", { exact: true })).toBeDisabled();
    await act(async () => release());
  });
});

describe("Tareas", () => {
  it("un doble envío de la captura crea una sola nota", async () => {
    let release: () => void = () => undefined;
    const calls = installFakeApi({ "GET /api/tareas/proximas": ok([]), "GET /api/tareas": ok([]) });
    const original = globalThis.fetch;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: string, init?: RequestInit) => {
        if (input !== "/api/capturas") return original(input, init);
        calls.push({ method: "POST", url: input, body: null, headers: {} });
        return new Promise<Response>((resolve) => {
          release = () => resolve(new Response(JSON.stringify({ ok: true, data: { path: "00-Inbox/x.md" } }), { status: 201 }));
        });
      }),
    );
    const user = userEvent.setup();
    render(<TasksView />);
    await user.type(await screen.findByLabelText(/Idea, tarea o recordatorio/), "Una idea");
    const save = screen.getByRole("button", { name: /Guardar/ });
    await user.click(save);
    await user.click(save);
    expect(calls.filter((c) => c.url === "/api/capturas")).toHaveLength(1);
    await act(async () => release());
  });

  it("la búsqueda no pide la API en cada tecla", async () => {
    const calls = installFakeApi({ "GET /api/tareas/proximas": ok([]), "GET /api/tareas": ok([]) });
    const user = userEvent.setup();
    render(<TasksView />);
    await user.type(await screen.findByLabelText("Buscar en el título"), "informe");
    await waitFor(() => expect(calls.some((c) => c.url === "/api/tareas?texto=informe")).toBe(true));
    expect(calls.filter((c) => c.url.startsWith("/api/tareas?texto=")).length).toBeLessThanOrEqual(2);
  });
});

describe("App", () => {
  it("cambiar de pestaña no borra la conversación con el Coach", async () => {
    installFakeApi({
      "GET /api/hoy": ok(briefing()),
      "GET /api/semana": ok(week()),
      "POST /api/checkins": { status: 201, body: { ok: true, data: { kind: "registro", message: "Anotado sin culpa.", micro_step: null, adjustment: null, path: "Diario/x.md", capture: null, ai_fallback: false, readable: true } } },
    });
    const user = userEvent.setup();
    render(<App />);
    await user.click(await screen.findByRole("button", { name: "Enviar check-in" }));
    expect(await screen.findByText("Anotado sin culpa.")).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "Semana" }));
    await user.click(screen.getByRole("tab", { name: "Hoy" }));
    expect(screen.getByText("Anotado sin culpa.")).toBeInTheDocument();
  });

  it("Inicio y Fin llevan a la primera y a la última pestaña", async () => {
    installFakeApi({ "GET /api/hoy": ok(briefing()), "GET /api/agentes": fail(500, "interno", "x") });
    const user = userEvent.setup();
    render(<App />);
    (await screen.findByRole("tab", { name: "Hoy" })).focus();
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Agentes" })).toHaveFocus();
    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: "Hoy" })).toHaveFocus();
  });

  it("el contenido principal es un landmark main que contiene el panel", async () => {
    installFakeApi({ "GET /api/hoy": ok(briefing()) });
    render(<App />);
    const main = await screen.findByRole("main");
    expect(within(main).getByRole("tabpanel")).toBeInTheDocument();
  });

  it("un error de render no deja la pantalla en blanco", async () => {
    installFakeApi({ "GET /api/hoy": ok({ ...briefing(), work: null }) });
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    render(<App />);
    expect(await screen.findByText(/Algo falló al mostrar esta vista/)).toBeInTheDocument();
  });
});
