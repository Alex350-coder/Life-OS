import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CalendarAvailability, CalendarStatus } from "../api/types";
import { block, fail, installFakeApi, ok, XSS } from "../test/fakeApi";
import { CalendarView } from "./CalendarView";

const CONNECTED: CalendarStatus = {
  configured: true,
  can_read: true,
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
      "GET /api/calendario": ok({ configured: false, grants: [], can_read: false }),
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
});
