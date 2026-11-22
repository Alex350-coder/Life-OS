// Oficina del Coach (F11-T08): el día de hoy, el check-in y el cierre con vista previa. El cierre solo se
// aplica al confirmar, y «Confirmar» se habilita después de ver la vista previa de esas mismas respuestas.
import { ApiError } from "../../api/client";
import type { Briefing, ClosingAnswer, ClosingPreview } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones } from "../acciones";
import { bloquear, campo, casilla, entrada, lista, pintar, seccion, selector, texto, Ventana } from "../ventana";

const ESTADOS = [
  ["nota", "Solo una nota"],
  ["empezando", "Empiezo"],
  ["hecho", "Terminé el bloque"],
  ["bloqueado", "Estoy bloqueado"],
  ["cansado", "Estoy cansado"],
  ["perdido", "Perdí el bloque"],
] as const;

const RESPUESTAS = [
  ["", "Sin respuesta"],
  ["hecha", "Hecha"],
  ["parcial", "Parcial"],
  ["no", "No la hice"],
] as const;

interface Fila {
  tarea: string;
  tipo: HTMLSelectElement;
  avance: HTMLInputElement;
  restante: HTMLInputElement;
}

/** Las respuestas del formulario de cierre (sin respuesta: la tarea no cambia). */
export function respuestas(filas: readonly Fila[]): ClosingAnswer[] {
  const out: ClosingAnswer[] = [];
  for (const f of filas) {
    const tipo = f.tipo.value;
    if (tipo !== "hecha" && tipo !== "parcial" && tipo !== "no") continue;
    const a: ClosingAnswer = { tarea: f.tarea, tipo };
    if (tipo === "parcial") {
      if (f.avance.value.trim() !== "") a.avance = f.avance.value.trim();
      if (f.restante.value.trim() !== "") a.restante = f.restante.value.trim();
    }
    out.push(a);
  }
  return out;
}

function tareasDeHoy(b: Briefing): [string, string][] {
  return [...new Map(b.work.filter((x) => x.task_id !== null).map((x) => [x.task_id ?? "", x.title])).entries()];
}

function cambios(p: ClosingPreview): string[] {
  return p.changes.map((c) => {
    if (c.kind === "hecha") return `${c.title} → completada`;
    if (c.kind === "no") return `${c.title} → no realizada: propuesta de reubicación`;
    return `${c.title} → avance ${c.progress_after ?? "?"}, restante ${c.remaining_after ?? "?"}`;
  });
}

export function crearCoach(refrescar: () => void): Ventana {
  const dia = h("div", { class: "lo-result" });
  const estado = selector(ESTADOS, "nota");
  const tarea = selector([["", "(ninguna)"]]);
  const mensaje = texto(2, "Cómo vas (opcional)");
  const captura = entrada("Captura rápida (opcional, va a 00-Inbox/)");
  const ia = casilla("Pedir el mensaje al modelo (IA)");
  const respuesta = h("div", { class: "lo-result" });
  const cierre = h("div", {});
  const previa = h("div", { class: "lo-result" });
  let filas: Fila[] = [];
  let vista: string | null = null; // respuestas de la última vista previa (JSON)

  const win: Ventana = new Ventana("coach", "Oficina del Coach", { rutas: ["hoy"], onOpen: () => void cargar() });

  const confirmar = win.boton("Confirmar cierre", "cierre", async () => {
    const r = await acciones.cierre(respuestas(filas));
    pintar(previa, lista([`Escrito en: ${r.written.join(", ")}`, ...cambios(r)]));
    win.aviso(r.adjustments.length > 0 ? `Cierre registrado. Reubicaciones para el Jefe: ${r.adjustments.length}.` : "Cierre registrado.", "ok");
    vista = null;
    bloquear(confirmar, true);
    refrescar();
  }, "primary");
  bloquear(confirmar, true);

  function invalidarPrevia(): void {
    vista = null;
    bloquear(confirmar, true);
    pintar(previa, );
  }

  function pintarDia(b: Briefing | null): void {
    if (b === null) {
      pintar(dia, h("p", { class: "lo-lead", text: "Sin plan para hoy: pide al Jefe que proponga la semana y apruébala." }));
      pintar(tarea, h("option", { text: "(ninguna)", attrs: { value: "" } }));
      pintar(cierre, );
      filas = [];
      return;
    }
    const bloques = b.blocks.map((x) => `${x.span} · ${x.title} (${x.kind})`);
    pintar(dia, 
      h("p", { class: "lo-lead", text: `${b.day} · ${b.approved ? "plan aprobado" : "plan sin aprobar"} · ${b.checkins} check-in(s)` }),
      h("p", { class: "lo-muted", text: `Ahora: ${b.current?.title ?? "nada"} · Luego: ${b.upcoming?.title ?? "nada más hoy"}` }),
      seccion("Bloques de hoy", lista(bloques, "Sin bloques.")),
      b.adjustments.length === 0 ? null : seccion("Ajustes para el Jefe", lista(b.adjustments.map((a) => `${a.at} · ${a.text}`))),
    );
    const tareas = tareasDeHoy(b);
    pintar(tarea, ...[["", "(ninguna)"] as const, ...tareas].map(([v, t]) => h("option", { text: t, attrs: { value: v } })));
    filas = tareas.map(([id]) => {
      const f: Fila = { tarea: id, tipo: selector(RESPUESTAS), avance: entrada("Avance %"), restante: entrada("Restante, p. ej. 1h30m") };
      for (const el of [f.tipo, f.avance, f.restante]) el.addEventListener("input", invalidarPrevia);
      return f;
    });
    pintar(cierre, 
      ...(filas.length === 0
        ? [h("p", { class: "lo-empty", text: "Hoy no hay tareas en el plan." })]
        : filas.map((f, i) => h("fieldset", { class: "lo-task" }, h("legend", { text: `${tareas[i]?.[1] ?? ""} · ${f.tarea}` }), campo("Respuesta", f.tipo), campo("Avance (%)", f.avance), campo("Restante", f.restante)))),
    );
    invalidarPrevia();
  }

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      try {
        pintarDia(await acciones.hoy());
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) pintarDia(null);
        else throw err;
      }
    });
  }

  win.body.append(
    dia,
    seccion(
      "Check-in",
      h("div", { class: "lo-row" }, campo("¿Cómo vas?", estado), campo("Tarea del bloque", tarea)),
      campo("Mensaje", mensaje),
      campo("Captura", captura),
      ia.el,
      h(
        "div",
        { class: "lo-actions" },
        win.boton("Enviar check-in", "checkin", async () => {
          const r = await acciones.checkin({ texto: mensaje.value, estado: estado.value, tarea: tarea.value || null, capturar: captura.value.trim() || null, ia: ia.input.checked });
          const paso = r.micro_step === null ? [] : [`Siguiente paso: ${r.micro_step.action} (${r.micro_step.minutes} min)`];
          pintar(respuesta, lista([r.message, ...paso, `Registrado en ${r.path}`, ...(r.capture === null ? [] : [`Captura: ${r.capture}`])]));
          mensaje.value = "";
          captura.value = "";
          win.aviso(r.ai_fallback ? "Check-in guardado (sin IA: se usó la respuesta local)." : "Check-in guardado.", "ok");
          refrescar();
          await cargar();
        }, "primary"),
      ),
      respuesta,
    ),
    seccion(
      "Cierre del día",
      h("p", { class: "lo-muted", text: "Solo cambia lo que respondas. Primero revisa la vista previa; nada se aplica sin confirmar." }),
      cierre,
      h(
        "div",
        { class: "lo-actions" },
        win.boton("Ver vista previa", "cierrePrevia", async () => {
          const r = respuestas(filas);
          const p = await acciones.cierrePrevia(r);
          pintar(previa, lista(cambios(p), "Ninguna tarea cambia."), p.missed.length === 0 ? null : seccion("Bloques perdidos", lista(p.missed.map((m) => `${m.span} · ${m.title}`))));
          vista = JSON.stringify(r);
          bloquear(confirmar, p.closed);
          win.aviso(p.closed ? "Este día ya está cerrado." : "Vista previa lista: nada se cambió todavía.", "info");
        }),
        confirmar,
      ),
      previa,
    ),
  );
  // Si las respuestas cambian después de la vista previa, hay que volver a verla.
  win.el.addEventListener("change", () => {
    if (vista !== null && vista !== JSON.stringify(respuestas(filas))) invalidarPrevia();
  });
  return win;
}
