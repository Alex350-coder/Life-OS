// Formatos compartidos por las ventanas del edificio (solo presentación).
import type { PlanBlock } from "../../api/types";
import type { Tono } from "../componentes";

const DIAS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];

/** 90 → «1 h 30 min»; 45 → «45 min»; 120 → «2 h». */
export function horas(minutos: number): string {
  const h = Math.floor(minutos / 60);
  const m = minutos % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** Fecha y hora ISO → «2026-11-02 07:30» (sin zona: la del servidor ya viene en el texto). */
export function momento(iso: string): string {
  return iso.replace("T", " ").slice(0, 16);
}

/** «2026-11-02» → «lun 02/11» (el día de la semana de esa fecha, sin depender de la zona del navegador). */
export function dia(iso: string): string {
  const [a, m, d] = iso.split("-");
  if (a === undefined || m === undefined || d === undefined) return iso;
  const n = new Date(Date.UTC(Number(a), Number(m) - 1, Number(d))).getUTCDay();
  return `${DIAS[n] ?? ""} ${d}/${m}`.trim();
}

export const TONO_BLOQUE: Readonly<Record<PlanBlock["kind"], Tono>> = { trabajo: "info", fijo: "neutro", protegido: "ok" };

const TONOS_ESTADO: Readonly<Record<string, Tono>> = {
  // tareas
  propuesta: "neutro",
  programada: "info",
  confirmada: "info",
  bloqueada: "error",
  completada: "ok",
  cancelada: "neutro",
  // especialistas
  borrador: "neutro",
  probado: "info",
  aprobado: "info",
  activo: "ok",
  desactivado: "alerta",
  // auditoría
  ok: "ok",
  intento: "neutro",
  rechazado: "error",
  error: "error",
};

export function tonoEstado(estado: string): Tono {
  return TONOS_ESTADO[estado] ?? "neutro";
}
