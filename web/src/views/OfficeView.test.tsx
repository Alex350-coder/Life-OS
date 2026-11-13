// Oficina de la Fase 9 (F9-T04): panel «Mi día», vista sencilla en un clic, charla en el tiempo
// libre y reacción a un evento real tras la recarga de cada minuto.
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Agents, Briefing, Indicators } from "../api/types";
import { block, briefing, installFakeApi, ok } from "../test/fakeApi";
import { OfficeView } from "./OfficeView";

const AGENTS: Agents = {
  month: "2026-11",
  agents: [],
  plan_owner: "jefe",
  forbidden_write: [],
  restricted_read: [],
  usage_malformed: 0,
};

const INDICATORS: Indicators = {
  start: "2026-08-05",
  end: "2026-11-02",
  streak: 4,
  focus_max_minutes: 240,
  deliveries: { due: 0, completed: 0, forgotten: 0, percent: null },
  adherence: { closed_days: 4, answered: 8, percent: 75 },
  usage: { active_days: 4, days_per_week: 0.3 },
  acceptance: { proposed: 0, approved: 0, minor_changes: 0, percent: null },
  costs: [],
  notifications: { sent: 0, failed: 0, skipped: 0, days_with_notifications: 0, max_per_day: 0, per_active_day: null, by_kind: [] },
  not_measured: [],
  quality: { malformed_log_lines: 0, unreadable_notes: 0 },
};

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.location.hash = "";
});

function free(overrides: Partial<Briefing> = {}): Briefing {
  const later = block({ title: "Guitarra", span: "16:00–17:00" });
  return briefing({ current: null, upcoming: later, checkins: 2, ...overrides });
}

describe("Oficina, Fase 9", () => {
  it("el panel Mi día muestra la racha, el foco y lo siguiente", async () => {
    installFakeApi({
      "GET /api/hoy": ok(free()),
      "GET /api/agentes": ok(AGENTS),
      "GET /api/indicadores": ok(INDICATORS),
    });
    render(<OfficeView />);
    const panel = await screen.findByRole("region", { name: "Mi día" });
    expect(await screen.findByText("4 día(s)")).toBeInTheDocument();
    expect(panel).toHaveTextContent("límite 4 h");
    expect(panel).toHaveTextContent("Guitarra (16:00–17:00)");
    expect(panel).toHaveTextContent("¡4 días seguidos cerrando el día!");
  });

  it("sin indicadores el panel no inventa la racha y la escena sigue funcionando", async () => {
    installFakeApi({ "GET /api/hoy": ok(free()), "GET /api/agentes": ok(AGENTS) });
    render(<OfficeView />);
    const panel = await screen.findByRole("region", { name: "Mi día" });
    await waitFor(() => expect(panel).toHaveTextContent("límite desconocido"));
    expect(panel).toHaveTextContent("Cada cierre cuenta.");
  });

  it("en el tiempo libre se ve en texto lo que charlan el Jefe y el Coach", async () => {
    installFakeApi({ "GET /api/hoy": ok(free()), "GET /api/agentes": ok(AGENTS) });
    render(<OfficeView />);
    expect(await screen.findByText(/Tiempo libre: Hoy van 2 check-in\(s\)/)).toBeInTheDocument();
  });

  it("la vista sencilla está a un clic", async () => {
    installFakeApi({ "GET /api/hoy": ok(free()), "GET /api/agentes": ok(AGENTS) });
    const user = userEvent.setup();
    render(<OfficeView />);
    await user.click(await screen.findByRole("button", { name: "Vista sencilla" }));
    expect(window.location.hash).toBe("#hoy");
  });

  it("al recargar celebra un check-in nuevo y lo anuncia en texto", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let calls = 0;
    installFakeApi({
      "GET /api/hoy": () => {
        calls += 1;
        return ok(free({ checkins: calls === 1 ? 2 : 3 }));
      },
      "GET /api/agentes": ok(AGENTS),
      "GET /api/indicadores": ok(INDICATORS),
    });
    render(<OfficeView />);
    await screen.findByText(/Hoy van 2 check-in/);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(await screen.findByRole("status")).toHaveTextContent("Último evento: ¡Check-in registrado!");
  });
});
