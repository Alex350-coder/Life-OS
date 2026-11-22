// Adaptador LifeOS → Habblaud (F11-T04): cada estado mostrado sale de una regla sobre datos reales.
import type { Agent, Agents, Briefing, HistoryEntry, ImportedAgent, PlanBlock, ReviewItem, Task } from "../api/types";
import { COACH_ROOM, JEFE_ROOM, OWNER_ID, OWNER_ROOM, toFeed, toSnapshot, type LifeosData } from "./adapter";

const NOW = Date.parse("2026-11-21T15:00:00-05:00");

function block(kind: PlanBlock["kind"], title: string): PlanBlock {
  return {
    kind,
    title,
    task_id: null,
    day: "2026-11-21",
    span: "14:00–16:00",
    start: "2026-11-21T14:00:00-05:00",
    end: "2026-11-21T16:00:00-05:00",
    minutes: 120,
  };
}

function briefing(patch: Partial<Briefing> = {}): Briefing {
  return {
    day: "2026-11-21",
    zone: "America/Lima",
    approved: true,
    readable: true,
    current: null,
    upcoming: null,
    resting: null,
    blocks: [],
    work: [],
    protected: [],
    checkins: 0,
    adjustments: [],
    rejected_adjustments: 0,
    ...patch,
  };
}

function core(id: string, name: string): Agent {
  return {
    id, name, version: 1, purpose: `Propósito de ${name}`, status: "activo", profile: "ligero",
    provider: "nvidia", model: "m", contract_in: "", contract_out: "", reads: [], writes: [],
    plan_writes: [], tools: [], metrics: [], usage: null,
  };
}

function specialist(id: string, state: ImportedAgent["state"], area = id): ImportedAgent {
  return {
    id, name: id.charAt(0).toUpperCase() + id.slice(1), purpose: "Proponer", area, profile: "ligero", state,
    current: 1, versions: [], history: [], effective: null, problems: [],
  };
}

function data(patch: Partial<LifeosData> = {}): LifeosData {
  const agents: Agents = {
    month: "2026-11", agents: [core("jefe", "Jefe"), core("coach", "Coach")], plan_owner: "jefe",
    forbidden_write: [], restricted_read: [], usage_malformed: 0, imported: [],
  };
  return { today: briefing(), agents, proposals: [], upcoming: [], history: [], now: NOW, ...patch };
}

function proposal(area: string, status = "pendiente"): ReviewItem {
  return {
    key: "p1", kind: "nueva_tarea", priority: "media", reason: "r", task_id: null, title: "T", effort: null,
    due: null, status, ref: `2026-W47/${area}/p1`, area, task_title: null, current_priority: null,
  };
}

function entry(actor: string, action: string, result: HistoryEntry["result"] = "ok"): HistoryEntry {
  return { ts: "2026-11-21T19:00:00Z", actor, action, target: "x", result, reason: null, completed: true };
}

const agent = (d: LifeosData, id: string) => toSnapshot(d, 1).agents.find((a) => a.id === id);

describe("toSnapshot: salas", () => {
  it("tiene tu espacio y las oficinas del Jefe y del Coach", () => {
    const ids = toSnapshot(data(), 1).rooms.map((r) => r.id);
    expect(ids).toEqual([OWNER_ROOM, JEFE_ROOM, COACH_ROOM]);
  });

  it("añade una sala por especialista registrado, en su orden, y nada más", () => {
    const d = data();
    d.agents.imported = [specialist("universidad", "activo"), specialist("hobbies", "borrador")];
    const rooms = toSnapshot(d, 1).rooms;
    expect(rooms.map((r) => r.name)).toEqual(["Tu espacio", "Oficina del Jefe", "Oficina del Coach", "Universidad", "Hobbies"]);
    expect(new Set(rooms.map((r) => r.slot)).size).toBe(rooms.length);
  });
});

describe("toSnapshot: estados con su regla", () => {
  it("tú trabajas solo durante un bloque de trabajo real", () => {
    const working = agent(data({ today: briefing({ current: block("trabajo", "Informe de redes") }) }), OWNER_ID);
    expect(working?.status).toBe("working");
    expect(working?.activity?.text).toContain("Informe de redes");
    expect(agent(data(), OWNER_ID)?.status).toBe("idle");
    expect(agent(data({ today: briefing({ resting: block("protegido", "Descanso") }) }), OWNER_ID)?.status).toBe("idle");
  });

  it("el Jefe espera si el plan no está aprobado o hay ajustes del Coach", () => {
    expect(agent(data({ today: briefing({ approved: false }) }), "jefe")).toMatchObject({ status: "waiting", waitingFor: "aprobar el plan de hoy" });
    const adj = { day: "2026-11-21", at: "10:00", kind: "reubicar", text: "x" };
    expect(agent(data({ today: briefing({ adjustments: [adj, adj] }) }), "jefe")?.waitingFor).toBe("revisar 2 ajustes del Coach");
    expect(agent(data(), "jefe")?.status).toBe("idle");
  });

  it("el Coach no inventa trabajo: siempre en reposo, con los check-ins reales", () => {
    const coach = agent(data({ today: briefing({ checkins: 3, current: block("trabajo", "X") }) }), "coach");
    expect(coach?.status).toBe("idle");
    expect(coach?.activity?.text).toBe("3 check-ins hoy");
  });

  it("un especialista espera si hay propuestas pendientes de su área", () => {
    const d = data({ proposals: [proposal("universidad"), proposal("universidad"), proposal("universidad", "aceptada"), proposal("hobbies")] });
    d.agents.imported = [specialist("universidad", "activo")];
    expect(agent(d, "universidad")).toMatchObject({ status: "waiting", waitingFor: "revisar 2 propuestas", roomId: "especialista:universidad" });
  });

  it("un especialista desactivado no aparece trabajando: queda fuera de la oficina", () => {
    const d = data();
    d.agents.imported = [specialist("carrera", "desactivado")];
    expect(agent(d, "carrera")?.status).toBe("offline");
  });
});

describe("toSnapshot: nada inventado", () => {
  it("sin cuentas, sesiones, modelos, tokens, shells, permisos ni mensajes de Claude Code", () => {
    const snap = toSnapshot(data(), 7);
    expect(snap.accounts).toEqual([]);
    expect(snap.meta).toMatchObject({ demo: false, sources: [] });
    for (const a of snap.agents) {
      expect(a.sessionId).toBe("");
      expect(a.model).toBeUndefined();
      expect(a.shells).toBeUndefined();
      expect(a.permission).toBeUndefined();
      expect(a.canMessage).toBeUndefined();
      expect(a.stats).toEqual({ toolCalls: 0, tokensIn: 0, tokensOut: 0, subagents: 0 });
    }
  });

  it("las tareas son las próximas reales del área del agente", () => {
    const tasks: Task[] = [
      { id: "t-1", title: "Entrega de BD", status: "pendiente", area: "universidad", effort: null, progress: null, priority: "alta", due: "2026-11-25", scheduled: null, path: "a", archived: false },
      { id: "t-2", title: "Gimnasio", status: "en_curso", area: "salud", effort: null, progress: null, priority: "media", due: null, scheduled: null, path: "b", archived: false },
    ];
    const d = data({ upcoming: tasks });
    d.agents.imported = [specialist("universidad", "activo")];
    expect(agent(d, "universidad")?.tasks).toEqual([{ id: "t-1", title: "Entrega de BD", status: "pending" }]);
    expect(agent(d, OWNER_ID)?.tasks.map((t) => t.status)).toEqual(["pending", "in_progress"]);
  });

  it("conserva statusSince si el estado no cambió", () => {
    const first = toSnapshot(data({ now: NOW }), 1);
    const later = toSnapshot(data({ now: NOW + 60_000 }), 2, first);
    expect(later.agents.find((a) => a.id === "jefe")?.statusSince).toBe(first.agents.find((a) => a.id === "jefe")?.statusSince);
  });
});

describe("toFeed", () => {
  it("convierte la auditoría real en actividad, sin los intentos", () => {
    const snap = toSnapshot(data(), 1);
    const feed = toFeed([entry("jefe", "plan_aprobar"), entry("jefe", "plan_aprobar", "intento"), entry("desconocido", "x")], snap);
    expect(feed).toHaveLength(1);
    expect(feed[0]).toMatchObject({ agentId: "jefe", roomId: JEFE_ROOM, agentName: "Jefe" });
    expect(feed[0]?.activity.text).toBe("plan aprobar");
  });

  it("marca como error lo rechazado o fallido", () => {
    const snap = toSnapshot(data(), 1);
    const [item] = toFeed([entry("propietario", "propuesta_aceptar", "rechazado")], snap);
    expect(item?.agentId).toBe(OWNER_ID);
    expect(item?.activity.error).toBe(true);
  });
});
