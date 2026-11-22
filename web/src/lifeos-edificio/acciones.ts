// Acciones de LifeOS en el edificio (F11-T06 a F11-T10, ADR-0022). Cada función llama a UNA ruta existente
// de la API con el cliente de siempre (sesión, CSRF, errores como ApiError). Aquí no se decide nada: el
// servidor valida, aplica los permisos y escribe en la bóveda. `RUTA` da la clave de rutas.json de cada una,
// para que la prueba de inventario compruebe que todas tienen botón o ventana.
import { checkSession, get, post, postVoid } from "../api/client";
import type {
  Agents,
  Briefing,
  CalendarAvailability,
  CalendarStatus,
  CheckInResult,
  ClosingAnswer,
  ClosingPreview,
  ClosingResult,
  Exported,
  History,
  ImportedAgent,
  Indicators,
  PlanResult,
  ReviewItem,
  RunResult,
  Task,
  UsageRow,
  Week,
} from "../api/types";

export type LifecycleAction = "aprobar" | "activar" | "desactivar" | "restaurar";

export interface TaskFilters {
  area?: string;
  estado?: string;
  texto?: string;
}

export interface CheckInInput {
  texto: string;
  estado: string;
  tarea: string | null;
  capturar: string | null;
  ia: boolean;
}

/** Semana vacía = la actual (el servidor decide cuál es). */
function week(semana: string): string | null {
  const value = semana.trim();
  return value === "" ? null : value;
}

function agentBase(id: string): string {
  return `/api/agentes/${encodeURIComponent(id)}`;
}

export const RUTA = {
  sesion: "GET /api/session",
  cerrarSesion: "POST /api/logout",
  hoy: "GET /api/hoy",
  semana: "GET /api/semana",
  proponer: "POST /api/plan/propuesta",
  aprobar: "POST /api/plan/aprobacion",
  replanificar: "POST /api/plan/revision",
  checkin: "POST /api/checkins",
  cierrePrevia: "POST /api/cierre/vista-previa",
  cierre: "POST /api/cierre",
  capturar: "POST /api/capturas",
  tareas: "GET /api/tareas",
  proximas: "GET /api/tareas/proximas",
  historial: "GET /api/historial",
  agentes: "GET /api/agentes",
  uso: "GET /api/uso",
  importar: "POST /api/agentes/importacion",
  probar: "POST /api/agentes/{agent_id}/prueba",
  estado: "POST /api/agentes/{agent_id}/estado",
  exportar: "GET /api/agentes/{agent_id}/exportacion",
  ejecutar: "POST /api/agentes/{agent_id}/ejecucion",
  propuestas: "GET /api/propuestas",
  aceptar: "POST /api/propuestas/aceptacion",
  rechazar: "POST /api/propuestas/rechazo",
  indicadores: "GET /api/indicadores",
  calendario: "GET /api/calendario",
  disponibilidad: "GET /api/calendario/disponibilidad",
} as const;

export type RutaId = keyof typeof RUTA;

export const acciones = {
  sesion: (): Promise<boolean> => checkSession(),
  cerrarSesion: (): Promise<void> => postVoid("/api/logout", {}),
  hoy: (): Promise<Briefing> => get<Briefing>("/api/hoy"),
  semana: (semana: string): Promise<Week> => get<Week>("/api/semana", { semana: semana.trim() }),
  proponer: (semana: string, ia: boolean): Promise<PlanResult> => post<PlanResult>("/api/plan/propuesta", { semana: week(semana), ia }),
  aprobar: (semana: string): Promise<PlanResult> => post<PlanResult>("/api/plan/aprobacion", { semana: week(semana) }),
  replanificar: (semana: string, ia: boolean): Promise<PlanResult> => post<PlanResult>("/api/plan/revision", { semana: week(semana), ia }),
  checkin: (input: CheckInInput): Promise<CheckInResult> => post<CheckInResult>("/api/checkins", input),
  cierrePrevia: (respuestas: ClosingAnswer[]): Promise<ClosingPreview> => post<ClosingPreview>("/api/cierre/vista-previa", { respuestas }),
  cierre: (respuestas: ClosingAnswer[]): Promise<ClosingResult> => post<ClosingResult>("/api/cierre", { respuestas }),
  capturar: (texto: string): Promise<{ path: string }> => post<{ path: string }>("/api/capturas", { texto }),
  tareas: (f: TaskFilters): Promise<Task[]> => get<Task[]>("/api/tareas", { area: f.area ?? "", estado: f.estado ?? "", texto: f.texto ?? "" }),
  proximas: (dias: number): Promise<Task[]> => get<Task[]>("/api/tareas/proximas", { dias }),
  historial: (limite: number): Promise<History> => get<History>("/api/historial", { limite }),
  agentes: (): Promise<Agents> => get<Agents>("/api/agentes"),
  uso: (): Promise<{ month: string; rows: UsageRow[]; total: UsageRow; malformed: number }> => get("/api/uso"),
  importar: (nombre: string, contenido: string): Promise<ImportedAgent> => post<ImportedAgent>("/api/agentes/importacion", { nombre, contenido }),
  probar: (id: string, ia: boolean): Promise<RunResult> => post<RunResult>(`${agentBase(id)}/prueba`, { ia }),
  estado: (id: string, accion: LifecycleAction): Promise<unknown> => post(`${agentBase(id)}/estado`, { accion }),
  exportar: (id: string): Promise<Exported> => get<Exported>(`${agentBase(id)}/exportacion`),
  ejecutar: (id: string, semana: string, ia: boolean): Promise<RunResult> => post<RunResult>(`${agentBase(id)}/ejecucion`, { semana: week(semana), ia }),
  propuestas: (): Promise<{ items: ReviewItem[]; skipped?: string[] }> => get("/api/propuestas"),
  aceptar: (ref: string): Promise<unknown> => post("/api/propuestas/aceptacion", { ref }),
  rechazar: (ref: string): Promise<unknown> => post("/api/propuestas/rechazo", { ref }),
  indicadores: (dias: number): Promise<Indicators> => get<Indicators>("/api/indicadores", { dias }),
  calendario: (): Promise<CalendarStatus> => get<CalendarStatus>("/api/calendario"),
  disponibilidad: (semana: string): Promise<CalendarAvailability> => get<CalendarAvailability>("/api/calendario/disponibilidad", { semana: semana.trim() }),
} as const;
