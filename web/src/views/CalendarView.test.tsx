import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CalendarAvailability, CalendarStatus } from "../api/types";
import { block, fail, installFakeApi, ok, XSS } from "../test/fakeApi";
import { CalendarView } from "./CalendarView";

const CONNECTED: CalendarStatus = {
  configured: true,
  can_read: true,
  problem: null,
  grants: [
    { grant: "lectura", scope: "calendar.freebusy", connected: true, obtained: "2026-10-30T00:00:00+00:00", problem: null },
    { grant: "escritura", scope: "calendar.app.created", connected: false, obtained: null, problem: null },
  ],
};

function availability(overrides: Partial<CalendarAvailability> = {}): CalendarAvailability {
  return {
    week: "2026-W45",
    zone: "America/Lima",
    plan_path: "Planes/2026-W45.md",
    plan_status: "aprobado",
    busy: [
      { day: "2026-11-02", span: "08:00–10:00", start: "2026-11-02T13:00:00+00:00", end: "2026-11-02T15:00:00+00:00", source: "google:primary", confidence: "alta" },
    ],
    clashes: [],
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("CalendarView", () => {
  it("explica cómo configurarlo y no pide la disponibilidad si no está configurado", async () => {
    const calls = installFakeApi({
      "GET /api/calendario": ok({ configured: false, grants: [], can_read: false, problem: null }),
    });
    render(<CalendarView />);
    expect(await screen.findByText(/no está configurado/)).toBeInTheDocument();
    expect(calls.some((call) => call.url.includes("disponibilidad"))).toBe(false);
  });

  it("muestra los permisos y lo ocupado, sin ningún botón que escriba en Google", async () => {
    installFakeApi({
      "GET /api/calendario": ok(CONNECTED),
      "GET /api/calendario/disponibilidad": ok(availability()),
    });
    render(<CalendarView />);
    expect(await screen.findByText(/2026-11-02 08:00–10:00 · google:primary · confianza alta/)).toBeInTheDocument();
    expect(screen.getByText(/conectado desde 2026-10-30/)).toBeInTheDocument();
    expect(screen.getByText(/sin conectar/)).toBeInTheDocument();
    expect(screen.getByText(/Ningún bloque de trabajo choca/)).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("avisa de cada choque con el plan y muestra los títulos como texto", async () => {
    const [busy] = availability().busy;
    if (busy === undefined) throw new Error("falta el intervalo de ejemplo");
    const clash = { block: block({ title: XSS, day: "2026-11-02", span: "08:00–09:00" }), busy };
    installFakeApi({
      "GET /api/calendario": ok(CONNECTED),
      "GET /api/calendario/disponibilidad": ok(availability({ clashes: [clash] })),
    });
    const { container } = render(<CalendarView />);
    expect(await screen.findByRole("alert")).toHaveTextContent(`Choque: «${XSS}»`);
    expect(container.querySelector("img")).toBeNull();
  });

  it("pide reiniciar si la lectura se conectó después de arrancar", async () => {
    installFakeApi({ "GET /api/calendario": ok({ ...CONNECTED, can_read: false }) });
    render(<CalendarView />);
    expect(await screen.findByText(/reinicia lifeos-web/)).toBeInTheDocument();
  });

  it("muestra el error de Google sin romper la vista", async () => {
    installFakeApi({
      "GET /api/calendario": ok(CONNECTED),
      "GET /api/calendario/disponibilidad": fail(409, "rechazado", "Google rechazó el token"),
    });
    render(<CalendarView />);
    expect(await screen.findByText(/Google rechazó el token/)).toBeInTheDocument();
  });

  it("agrupa varios choques en un solo aviso, sin claves repetidas", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => undefined);
    const [busy] = availability().busy;
    if (busy === undefined) throw new Error("falta el intervalo de ejemplo");
    const other = { ...busy, source: "google:trabajo" };
    const shared = block({ title: "Informe", day: "2026-11-02", span: "08:00–09:00" });
    installFakeApi({
      "GET /api/calendario": ok(CONNECTED),
      "GET /api/calendario/disponibilidad": ok(
        availability({ busy: [busy, other], clashes: [{ block: shared, busy }, { block: shared, busy: other }] }),
      ),
    });
    render(<CalendarView />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("2 choques con el calendario");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(errors).not.toHaveBeenCalled();
    errors.mockRestore();
  });

  it("pide otra semana y no consulta mientras la semana escrita no es válida", async () => {
    const calls = installFakeApi({
      "GET /api/calendario": ok(CONNECTED),
      "GET /api/calendario/disponibilidad": ok(availability()),
    });
    render(<CalendarView />);
    await screen.findByText(/confianza alta/);
    const input = screen.getByRole("textbox", { name: /Semana/ });
    await userEvent.type(input, "2026-W4");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByText(/Corrígelo para ver la disponibilidad/)).toBeInTheDocument();
    expect(screen.queryByText(/confianza alta/)).toBeNull();
    await userEvent.type(input, "6");
    await screen.findByText(/confianza alta/);
    const asked = calls.filter((call) => call.url.includes("disponibilidad")).map((call) => call.url);
    expect(asked).toEqual(["/api/calendario/disponibilidad", "/api/calendario/disponibilidad?semana=2026-W46"]);
  });

  it("muestra una configuración rechazada como problema", async () => {
    installFakeApi({ "GET /api/calendario": ok({ ...CONNECTED, grants: [], can_read: false, problem: "formato no válido" }) });
    render(<CalendarView />);
    expect(await screen.findByText(/Problema de configuración: formato no válido/)).toBeInTheDocument();
  });
});
