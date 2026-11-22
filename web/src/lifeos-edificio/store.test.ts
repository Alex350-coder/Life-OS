// LifeosStore (F11-T05): fuente de datos del edificio sobre las rutas existentes, con errores visibles.
import type { Agents, Briefing, HistoryEntry } from "../api/types";
import type { LifeosData } from "./adapter";
import { ApiError } from "../api/client";
import { LifeosStore, REFRESH_MS, todayOrNull } from "./store";

const T0 = Date.parse("2026-11-21T15:00:00-05:00");

function data(history: HistoryEntry[] = [], approved = true): Omit<LifeosData, "now"> {
  const today: Briefing = {
    day: "2026-11-21", zone: "America/Lima", approved, readable: true, current: null, upcoming: null,
    resting: null, blocks: [], work: [], protected: [], checkins: 0, adjustments: [], rejected_adjustments: 0,
  };
  const agents: Agents = {
    month: "2026-11", agents: [], plan_owner: "jefe", forbidden_write: [], restricted_read: [], usage_malformed: 0,
  };
  return { today, agents, proposals: [], upcoming: [], history };
}

function entry(ts: string, action: string): HistoryEntry {
  return { ts, actor: "jefe", action, target: "Semanal/2026-W47.md", result: "ok", reason: null, completed: true };
}

function setup(load: () => Promise<Omit<LifeosData, "now">>) {
  let now = T0;
  const store = new LifeosStore({ load, now: () => now });
  return { store, advance: (ms: number) => (now += ms) };
}

describe("LifeosStore", () => {
  it("empieza conectando y publica el snapshot real al cargar", async () => {
    const { store } = setup(async () => data());
    const seen: string[] = [];
    store.on("connection", (c) => seen.push(c));
    expect(store.connection).toBe("connecting");
    await store.refresh();
    expect(store.connection).toBe("open");
    expect(store.snapshot?.agents.map((a) => a.id)).toContain("jefe");
    expect(store.agent("jefe")?.roomId).toBe("oficina:jefe");
    expect(store.agentsInRoom("oficina:coach").map((a) => a.id)).toEqual(["coach"]);
    expect(seen).toEqual(["open"]);
  });

  it("nunca es una demostración ni un timelapse", () => {
    const { store } = setup(async () => data());
    expect(store.mock).toBe(false);
    expect(store.replaying).toBe(false);
  });

  it("si una ruta falla, se cierra la conexión, conserva lo último y programa el reintento", async () => {
    let fail = false;
    const { store } = setup(async () => {
      if (fail) throw new Error("sin conexión");
      return data();
    });
    await store.refresh();
    const before = store.snapshot;
    fail = true;
    await store.refresh();
    expect(store.connection).toBe("closed");
    expect(store.snapshot).toBe(before);
    expect(store.nextRetryAt).toBe(T0 + REFRESH_MS);
    expect(store.lastError).toBe("sin conexión");
  });

  it("la actividad sale del historial real, del más antiguo al más nuevo", async () => {
    const { store } = setup(async () => data([entry("2026-11-21T19:00:00Z", "plan_aprobar"), entry("2026-11-21T18:00:00Z", "plan_proponer")]));
    await store.refresh();
    expect(store.feed.map((f) => f.activity.text)).toEqual(["plan proponer", "plan aprobar"]);
  });

  it("avisa de lo nuevo entre dos recargas, aunque la página estuviera oculta (DT-36)", async () => {
    let history = [entry("2026-11-21T18:00:00Z", "plan_proponer")];
    const { store, advance } = setup(async () => data(history));
    const notices: string[] = [];
    store.on("notice", (n) => notices.push(n.text));
    await store.refresh();
    expect(notices).toEqual([]); // lo que ya estaba no es nuevo
    history = [...history, entry("2026-11-21T19:00:00Z", "plan_aprobar")];
    advance(REFRESH_MS);
    await store.refresh();
    expect(notices).toEqual(["Jefe: plan aprobar"]);
  });

  it("los suscriptores dejan de recibir al darse de baja", async () => {
    const { store } = setup(async () => data());
    let calls = 0;
    const off = store.on("snapshot", () => calls++);
    await store.refresh();
    off();
    await store.refresh();
    expect(calls).toBe(1);
  });
});

describe("todayOrNull (hallazgo 1)", () => {
  it("el 409 de /api/hoy significa «no hay plan para hoy», no un fallo de conexión", async () => {
    const noPlan = new ApiError(409, { code: "rechazado", message: "no hay plan para 2026-11-02" });
    await expect(todayOrNull(async () => Promise.reject(noPlan))).resolves.toBeNull();
  });

  it("cualquier otro error se propaga y cierra la conexión", async () => {
    const lost = new ApiError(401, { code: "sesion", message: "sin sesión" });
    await expect(todayOrNull(async () => Promise.reject(lost))).rejects.toBe(lost);
    const broken = new ApiError(500, { code: "interno", message: "fallo" });
    await expect(todayOrNull(async () => Promise.reject(broken))).rejects.toBe(broken);
  });

  it("sin plan, el store publica el edificio y la conexión queda abierta", async () => {
    const { store } = setup(async () => ({ ...data(), today: null }));
    await store.refresh();
    expect(store.connection).toBe("open");
    expect(store.agent("jefe")?.status).toBe("waiting");
  });
});
