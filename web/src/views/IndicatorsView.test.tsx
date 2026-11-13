import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Indicators } from "../api/types";
import { fail, installFakeApi, ok, XSS } from "../test/fakeApi";
import { IndicatorsView } from "./IndicatorsView";

function indicators(overrides: Partial<Indicators> = {}): Indicators {
  return {
    start: "2026-10-06",
    end: "2026-11-02",
    streak: 2,
    focus_max_minutes: 240,
    deliveries: { due: 4, completed: 3, forgotten: 1, percent: 75 },
    adherence: { closed_days: 0, answered: 0, percent: null },
    usage: { active_days: 8, days_per_week: 2 },
    acceptance: { proposed: 4, approved: 3, minor_changes: 2, percent: 67 },
    costs: [],
    notifications: {
      sent: 5, failed: 1, skipped: 0, days_with_notifications: 3, max_per_day: 2, per_active_day: 1.7,
      by_kind: [{ kind: "briefing", count: 3 }],
    },
    not_measured: [{ name: "Utilidad y nivel de estrés", reason: "valoración del propietario" }],
    quality: { malformed_log_lines: 0, unreadable_notes: 0, invalid_task_lines: 0, unreadable_closings: 0 },
    ...overrides,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("IndicatorsView", () => {
  it("muestra los indicadores y lo desconocido como desconocido, no como 0", async () => {
    installFakeApi({ "GET /api/indicadores": ok(indicators()) });
    render(<IndicatorsView />);
    expect(await screen.findByText("75 %")).toBeInTheDocument();
    expect(screen.getByText("desconocido")).toBeInTheDocument();
    expect(screen.getByText(/5 enviadas, 1 fallidas/)).toBeInTheDocument();
    expect(screen.getByText(/Sin llamadas a modelos/)).toBeInTheDocument();
    expect(screen.getByText("Utilidad y nivel de estrés:")).toBeInTheDocument();
  });

  it("cambia la ventana pidiendo otros días", async () => {
    const calls = installFakeApi({ "GET /api/indicadores": ok(indicators()) });
    render(<IndicatorsView />);
    await screen.findByText("75 %");
    await userEvent.selectOptions(screen.getByRole("combobox"), "7");
    await screen.findByText("75 %");
    expect(calls.map((call) => call.url)).toContain("/api/indicadores?dias=7");
  });

  it("muestra los costos como texto y avisa de las líneas ilegibles", async () => {
    const data = indicators({
      costs: [{ agent: XSS, ritual: "propuesta", calls: 2, errors: 0, latency_median_ms: 800, latency_p95_ms: 1200, cost_estimated_usd: "0", cost_unknown: 2 }],
      quality: { malformed_log_lines: 3, unreadable_notes: 0, invalid_task_lines: 2, unreadable_closings: 1 },
    });
    installFakeApi({ "GET /api/indicadores": ok(data) });
    const { container } = render(<IndicatorsView />);
    expect(await screen.findByText(/2 sin costo conocido/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    expect(
      screen.getByText(/No cuentan: 3 líneas de log ilegibles; 2 líneas de tareas con formato no válido; 1 cierres/),
    ).toBeInTheDocument();
  });

  it("muestra el error del servidor", async () => {
    installFakeApi({ "GET /api/indicadores": fail(400, "rechazado", "falta _sistema/horario.md") });
    render(<IndicatorsView />);
    expect(await screen.findByText(/falta _sistema\/horario.md/)).toBeInTheDocument();
  });
});
