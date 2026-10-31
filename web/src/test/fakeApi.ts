// `fetch` falso para las pruebas de componentes (aislado: nunca se usa fuera de src/**/*.test.*).
// Responde con el mismo sobre que la API real y registra cada petición.
import { vi } from "vitest";
import { resetSession } from "../api/client";
import type { Briefing, PlanBlock, Week } from "../api/types";

export interface Recorded {
  method: string;
  url: string;
  body: unknown;
  headers: Record<string, string>;
}

type Reply = { status?: number; body: unknown } | ((request: Recorded) => { status?: number; body: unknown });

export function installFakeApi(routes: Record<string, Reply>): Recorded[] {
  resetSession();
  const calls: Recorded[] = [];
  const all: Record<string, Reply> = {
    "GET /api/session": { body: { ok: true, data: { csrf_token: "csrf-de-prueba" } } },
    ...routes,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const method = init.method ?? "GET";
      const path = input.split("?")[0] ?? input;
      const request: Recorded = {
        method,
        url: input,
        body: typeof init.body === "string" ? JSON.parse(init.body) : null,
        headers: (init.headers as Record<string, string> | undefined) ?? {},
      };
      calls.push(request);
      const reply = all[`${method} ${path}`];
      if (reply === undefined) {
        return new Response(JSON.stringify({ ok: false, error: { code: "no_encontrado", message: `sin ruta ${method} ${path}` } }), { status: 404 });
      }
      const { status = 200, body } = typeof reply === "function" ? reply(request) : reply;
      return new Response(status === 204 ? null : JSON.stringify(body), { status });
    }),
  );
  return calls;
}

export const ok = <T,>(data: T) => ({ body: { ok: true, data } });
export const fail = (status: number, code: string, message: string, data: unknown = undefined) => ({
  status,
  body: { ok: false, error: { code, message }, ...(data === undefined ? {} : { data }) },
});

export const XSS = "<img src=x onerror=alert(1)>";

export function block(overrides: Partial<PlanBlock> = {}): PlanBlock {
  return {
    kind: "trabajo",
    title: "Informe de redes",
    task_id: "t-aaaaaa",
    day: "2026-11-02",
    span: "08:00–09:30",
    start: "2026-11-02T08:00:00-05:00",
    end: "2026-11-02T09:30:00-05:00",
    minutes: 90,
    ...overrides,
  };
}

export function briefing(overrides: Partial<Briefing> = {}): Briefing {
  const work = block();
  return {
    day: "2026-11-02",
    zone: "America/Lima",
    approved: true,
    readable: true,
    current: work,
    upcoming: null,
    resting: null,
    blocks: [work],
    work: [work],
    protected: [],
    checkins: 0,
    adjustments: [],
    rejected_adjustments: 0,
    ...overrides,
  };
}

export function week(overrides: Partial<Week> = {}): Week {
  const days = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07", "2026-11-08"];
  return {
    week: "2026-W45",
    zone: "America/Lima",
    path: "Semanal/2026-W45.md",
    status: "propuesto",
    origin: "heuristica",
    revision: 0,
    versions: 1,
    generated: "2026-11-01T20:00:00-05:00",
    justification: "—",
    days,
    blocks: [block()],
    load: days.map((day, index) => ({
      day,
      budget_minutes: index === 6 ? 0 : 240,
      planned_minutes: index === 0 ? 300 : 0,
      fixed_minutes: index === 0 ? 120 : 0,
      protected_minutes: index === 6 ? 1440 : 0,
      free_minutes: 600,
      over_budget: index === 0,
    })),
    ...overrides,
  };
}
