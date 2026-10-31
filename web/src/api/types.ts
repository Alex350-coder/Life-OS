// Contrato de la API local (src/lifeos/web/serialize.py). Solo datos: el frontend no decide nada.

export interface PlanBlock {
  kind: "trabajo" | "fijo" | "protegido";
  title: string;
  task_id: string | null;
  day: string;
  span: string;
  start: string;
  end: string;
  minutes: number;
}

export interface Adjustment {
  day: string;
  at: string;
  kind: string;
  text: string;
}

export interface Briefing {
  day: string;
  zone: string;
  approved: boolean;
  readable: boolean;
  current: PlanBlock | null;
  upcoming: PlanBlock | null;
  resting: PlanBlock | null;
  blocks: PlanBlock[];
  work: PlanBlock[];
  protected: PlanBlock[];
  checkins: number;
  adjustments: Adjustment[];
  rejected_adjustments: number;
}

export interface CheckInResult {
  kind: string;
  message: string;
  micro_step: { action: string; minutes: number } | null;
  adjustment: Adjustment | null;
  path: string;
  capture: string | null;
  ai_fallback: boolean;
  readable: boolean;
}

export interface DayLoad {
  day: string;
  budget_minutes: number;
  planned_minutes: number;
  fixed_minutes: number;
  protected_minutes: number;
  free_minutes: number;
  over_budget: boolean;
}

export interface Week {
  week: string;
  zone: string;
  path: string | null;
  status: "propuesto" | "aprobado" | null;
  origin: string | null;
  revision: number;
  versions: number;
  generated: string | null;
  justification: string;
  days: string[];
  blocks: PlanBlock[];
  load: DayLoad[];
}

export interface Unplanned {
  task_id: string | null;
  title: string;
  minutes: number;
  reason: string;
  suggestion: string;
}

export interface Conflict {
  code: string;
  message: string;
  day: string | null;
}

export interface PlanResult {
  accepted: boolean;
  status: string | null;
  path: string | null;
  proposal: {
    week: string;
    origin: string;
    work: PlanBlock[];
    unplanned: Unplanned[];
    alerts: string[];
    justification: string;
  };
  conflicts: Conflict[];
}

export interface Task {
  id: string | null;
  title: string;
  status: string;
  area: string | null;
  effort: string | null;
  progress: string | null;
  priority: string;
  due: string | null;
  scheduled: string | null;
  path: string;
  archived: boolean;
}

export interface TaskChange {
  task_id: string;
  title: string;
  kind: "hecha" | "parcial" | "no";
  progress_before: string | null;
  progress_after: string | null;
  remaining_before: string | null;
  remaining_after: string | null;
}

export interface ClosingPreview {
  day: string;
  path: string;
  changes: TaskChange[];
  unanswered: string[];
  missed: PlanBlock[];
  closed: boolean;
}

export interface ClosingResult extends ClosingPreview {
  written: string[];
  adjustments: Adjustment[];
}

export interface HistoryEntry {
  ts: string;
  actor: string;
  action: string;
  target: string;
  result: "intento" | "ok" | "rechazado" | "error";
  reason: string | null;
  completed: boolean;
}

export interface History {
  entries: HistoryEntry[];
  malformed: number;
  truncated: boolean;
}

export interface UsageRow {
  agent: string;
  ritual: string;
  calls: number;
  attempts: number;
  errors: number;
  tokens_prompt: number;
  tokens_completion: number;
  tokens_unknown: number;
  cost_estimated_usd: string;
  cost_unknown: number;
}

export interface Grant {
  root: string;
  mode: string;
  section: string | null;
}

export interface Agent {
  id: string;
  name: string;
  version: number;
  purpose: string;
  status: string;
  profile: string;
  provider: string;
  model: string;
  contract_in: string;
  contract_out: string;
  reads: string[];
  writes: Grant[];
  plan_writes: Grant[];
  tools: string[];
  metrics: string[];
  usage: UsageRow | null;
}

export interface Agents {
  month: string;
  agents: Agent[];
  plan_owner: string;
  forbidden_write: string[];
  restricted_read: string[];
  usage_malformed: number;
}

export interface ClosingAnswer {
  tarea: string;
  tipo: "hecha" | "parcial" | "no";
  avance?: string;
  restante?: string;
}
