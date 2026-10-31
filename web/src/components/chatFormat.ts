// Texto de las respuestas del chat: solo presenta el resultado del caso de uso (como
// src/lifeos/cli/output.py). No decide nada: lo decidido viene en la respuesta de la API.
import type { CheckInResult, PlanResult } from "../api/types";

export function coachReply(result: CheckInResult): string[] {
  const lines = [result.message];
  if (result.micro_step !== null) {
    lines.push(`Micro-paso (${result.micro_step.minutes} min): ${result.micro_step.action}`);
  }
  if (result.adjustment !== null) {
    lines.push(`Propuesta para el Jefe registrada: ${result.adjustment.text}. Revísala replanificando.`);
  }
  if (result.capture !== null) lines.push(`Captura guardada en ${result.capture}`);
  if (!result.readable) {
    lines.push("Aviso: la nota de hoy no sigue el formato del plan; no se usaron sus bloques.");
  }
  if (result.ai_fallback) lines.push("(El modelo no respondió bien; se usó la respuesta sin IA.)");
  lines.push(`Registrado en ${result.path}`);
  return lines;
}

export function jefeReply(result: PlanResult): string[] {
  const week = result.proposal.week;
  if (!result.accepted) {
    return [
      `Plan de ${week} rechazado: ${result.conflicts.length} conflicto(s). No se guardó nada.`,
      ...result.conflicts.map((c) => `[${c.code}] ${c.message}`),
    ];
  }
  if (result.status === "aprobado") {
    return [`Plan ${week} aprobado y publicado en ${result.path ?? "?"} y en las notas de Diario/.`];
  }
  const lines = [
    `Plan propuesto para ${week} guardado en ${result.path ?? "?"} (origen: ${result.proposal.origin}).`,
    `Bloques de trabajo: ${result.proposal.work.length}.`,
  ];
  for (const item of result.proposal.unplanned) {
    lines.push(`No cabe: ${item.title} · ${item.minutes} min · ${item.reason} → ${item.suggestion}`);
  }
  for (const alert of result.proposal.alerts) lines.push(`Alerta: ${alert}`);
  lines.push("Revisa la semana y, si estás de acuerdo, apruébala.");
  return lines;
}
