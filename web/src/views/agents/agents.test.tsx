import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { resetSession } from "../../api/client";
import type { Agents, ImportedAgent, ReviewItem, RunResult } from "../../api/types";
import { fail, installFakeApi, ok, XSS } from "../../test/fakeApi";
import { AgentsView } from "../AgentsView";

afterEach(() => {
  vi.unstubAllGlobals();
  resetSession();
});

function imported(overrides: Partial<ImportedAgent> = {}): ImportedAgent {
  return {
    id: "universidad",
    name: "Universidad",
    purpose: "Proponer prioridades del área.",
    area: "universidad",
    profile: "ligero",
    state: "borrador",
    current: 1,
    versions: [{ number: 1, config_version: 1, sha256: "abcdef012345", imported_at: "2026-11-01T20:00:00-05:00" }],
    history: [],
    effective: {
      reads: ["01-Universidad/"],
      writes: [{ root: "Propuestas/", mode: "crear", section: null }],
      tools: ["leer_nota", "listar_tareas", "proponer_al_jefe"],
    },
    problems: [],
    ...overrides,
  };
}

function agents(list: ImportedAgent[] = []): Agents {
  return {
    month: "2026-11",
    agents: [],
    plan_owner: "jefe",
    forbidden_write: ["_sistema/"],
    restricted_read: ["05-Salud/"],
    usage_malformed: 0,
    imported: list,
  };
}

const review: ReviewItem = {
  ref: "2026-W45/universidad/p1",
  key: "p1",
  kind: "prioridad",
  priority: "alta",
  reason: "vence el 2026-11-03",
  task_id: "t-abc234",
  title: null,
  effort: null,
  due: null,
  status: "pendiente",
  area: "universidad",
  task_title: "Informe",
  current_priority: "media",
};

const run: RunResult = {
  agent_id: "universidad",
  week: "2026-W45",
  source: "heuristica",
  dry_run: true,
  recorded_test: true,
  path: null,
  items: [{ ...review }],
  alerts: ["1 líneas de metas no se entendieron"],
};

describe("Agentes importados", () => {
  it("importar envía el YAML tal cual y no activa nada", async () => {
    const calls = installFakeApi({
      "GET /api/agentes": ok(agents()),
      "GET /api/propuestas": ok({ items: [] }),
      "POST /api/agentes/importacion": { status: 201, body: { ok: true, data: imported() } },
    });
    const user = userEvent.setup();
    render(<AgentsView />);
    await user.type(await screen.findByLabelText("Configuración YAML"), "id: universidad");
    await user.click(screen.getByRole("button", { name: "Importar en borrador" }));
    expect(await screen.findByText(/v1 en borrador/)).toBeInTheDocument();
    const sent = calls.find((c) => c.url === "/api/agentes/importacion");
    expect(sent?.body).toEqual({ nombre: "agente.yaml", contenido: "id: universidad" });
    expect(sent?.headers["X-CSRF-Token"]).toBe("csrf-de-prueba");
  });

  it("un rechazo muestra cada violación como texto", async () => {
    installFakeApi({
      "GET /api/agentes": ok(agents()),
      "GET /api/propuestas": ok({ items: [] }),
      "POST /api/agentes/importacion": fail(422, "agente_rechazado", "no válida", {
        violations: ["universidad: lectura '05-Salud' está en un área restringida", XSS],
      }),
    });
    const user = userEvent.setup();
    const { container } = render(<AgentsView />);
    await user.type(await screen.findByLabelText("Configuración YAML"), "x");
    await user.click(screen.getByRole("button", { name: "Importar en borrador" }));
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText(/05-Salud/)).toBeInTheDocument();
    expect(within(alert).getByText(XSS)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("los botones siguen el estado y probar muestra el resultado sin escribir", async () => {
    const calls = installFakeApi({
      "GET /api/agentes": ok(agents([imported()])),
      "GET /api/propuestas": ok({ items: [] }),
      "POST /api/agentes/universidad/prueba": ok(run),
    });
    const user = userEvent.setup();
    render(<AgentsView />);
    const card = (await screen.findByRole("heading", { name: /Universidad/ })).closest("article");
    expect(card).not.toBeNull();
    const scope = within(card as HTMLElement);
    expect(scope.queryByRole("button", { name: "Activar" })).toBeNull();
    expect(scope.getByRole("button", { name: "Ejecutar" })).toBeDisabled();
    await user.click(scope.getByRole("button", { name: "Probar" }));
    expect(await scope.findByText(/Prueba sin escrituras/)).toBeInTheDocument();
    expect(scope.getByText(/metas no se entendieron/)).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/agentes/universidad/prueba" && c.method === "POST")).toBe(true);
  });

  it("restaurar envía la versión elegida", async () => {
    const two = imported({
      state: "activo",
      current: 2,
      versions: [
        { number: 1, config_version: 1, sha256: "aaaaaaaaaaaa", imported_at: "2026-11-01T20:00:00-05:00" },
        { number: 2, config_version: 2, sha256: "bbbbbbbbbbbb", imported_at: "2026-11-02T20:00:00-05:00" },
      ],
    });
    const calls = installFakeApi({
      "GET /api/agentes": ok(agents([two])),
      "GET /api/propuestas": ok({ items: [] }),
      "POST /api/agentes/universidad/estado": ok(two),
    });
    const user = userEvent.setup();
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "Restaurar" }));
    await waitFor(() =>
      expect(calls.find((c) => c.url === "/api/agentes/universidad/estado")?.body).toEqual({
        accion: "restaurar",
        version: 1,
      }),
    );
  });

  it("un problema de política se avisa y el contenido se muestra como texto", async () => {
    installFakeApi({
      "GET /api/agentes": ok(agents([imported({ purpose: XSS, problems: ["universidad: excede la política"] })])),
      "GET /api/propuestas": ok({ items: [] }),
    });
    const { container } = render(<AgentsView />);
    expect(await screen.findByText(/excede la política/)).toBeInTheDocument();
    expect(screen.getByText(XSS)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});

describe("Propuestas", () => {
  it("aceptar envía la referencia y recarga la lista", async () => {
    let pending: ReviewItem[] = [review];
    const calls = installFakeApi({
      "GET /api/agentes": ok(agents()),
      "GET /api/propuestas": () => ok({ items: pending }),
      "POST /api/propuestas/aceptacion": () => {
        pending = [];
        return ok({ ref: review.ref, task_id: "t-abc234" });
      },
    });
    const user = userEvent.setup();
    render(<AgentsView />);
    expect(await screen.findByText(/media →/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Aceptar" }));
    expect(await screen.findByText(`Aceptada ${review.ref}.`)).toBeInTheDocument();
    expect(await screen.findByText("No hay propuestas pendientes.")).toBeInTheDocument();
    expect(calls.find((c) => c.url === "/api/propuestas/aceptacion")?.body).toEqual({ ref: review.ref });
  });

  it("un error al rechazar se muestra sin perder la lista", async () => {
    installFakeApi({
      "GET /api/agentes": ok(agents()),
      "GET /api/propuestas": ok({ items: [review] }),
      "POST /api/propuestas/rechazo": fail(409, "rechazado", "propuesta: ya está aceptada"),
    });
    const user = userEvent.setup();
    render(<AgentsView />);
    await user.click(await screen.findByRole("button", { name: "Rechazar" }));
    expect(await screen.findByText("propuesta: ya está aceptada")).toBeInTheDocument();
    expect(screen.getByText(review.ref)).toBeInTheDocument();
  });
});
