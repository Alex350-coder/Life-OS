// Oficina del Coach (F11-T08): el día de hoy, el check-in y el cierre con vista previa. El cierre solo se
// aplica al confirmar, y «Confirmar» se habilita después de ver la vista previa de esas mismas respuestas.
import { ApiError } from "../../api/client";
import type { Briefing, ClosingAnswer, ClosingPreview } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones } from "../acciones";
import { etiqueta, fila, lineas, paginar, pestanas } from "../componentes";
import { bloquear, campo, casilla, entrada, opcion, pintar, seccion, selector, texto, Ventana } from "../ventana";
import { dia, TONO_BLOQUE } from "./formato";

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

interface Respuesta {
  tarea: string;
  titulo: string;
  tipo: HTMLSelectElement;
  avance: HTMLInputElement;
  restante: HTMLInputElement;
}

/** Una tarea del cierre; «avance» y «restante» solo se piden si quedó a medias. */
function formulario(r: Respuesta): HTMLElement {
  const parcial = h("div", { class: "lo-row" }, campo("Avance (%)", r.avance), campo("Restante", r.restante));
  const ver = (): void => {
    parcial.hidden = r.tipo.value !== "parcial";
  };
  r.tipo.addEventListener("input", ver);
  ver();
  return h("fieldset", { class: "lo-task" }, h("legend", { class: "lo-task__title", text: `${r.titulo} · ${r.tarea}` }), campo("Respuesta", r.tipo), parcial);
}

/** Las respuestas del formulario de cierre (sin respuesta: la tarea no cambia). */
export function respuestas(filas: readonly Respuesta[]): ClosingAnswer[] {
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

function cabecera(b: Briefing): HTMLElement[] {
  return [
    h(
      "div",
      { class: "lo-lead" },
      h("span", { class: "lo-lead__text", text: dia(b.day) }),
      etiqueta({ texto: b.approved ? "plan aprobado" : "plan sin aprobar", tono: b.approved ? "ok" : "alerta" }),
      etiqueta({ texto: `${b.checkins} check-in(s)` }),
    ),
    h("p", { class: "lo-muted", text: `Ahora: ${b.current?.title ?? "nada"} · Luego: ${b.upcoming?.title ?? "nada más hoy"}` }),
  ];
}

export function crearCoach(refrescar: () => void): Ventana {
  const titulo = h("div", { class: "lo-result" });
  const hoy = h("div", { class: "lo-result" });
  const estado = selector(ESTADOS, "nota");
  const tarea = selector([["", "(ninguna)"]]);
  const mensaje = texto(2, "Cómo vas (opcional)");
  const captura = entrada("Captura rápida (opcional, va a 00-Inbox/)");
  const ia = casilla("Pedir el mensaje al modelo (IA)");
  const respuesta = h("div", { class: "lo-result" });
  const cierre = h("div", { class: "lo-result" });
  const previa = h("div", { class: "lo-result" });
  let filas: Respuesta[] = [];
  let vista: string | null = null; // respuestas de la última vista previa (JSON)

  const win: Ventana = new Ventana("coach", "Oficina del Coach", { rutas: ["hoy"], onOpen: () => void cargar() });

  const confirmar = win.boton("Confirmar cierre", "cierre", async () => {
    const r = await acciones.cierre(respuestas(filas));
    pintar(previa, lineas([`Escrito en: ${r.written.join(", ")}`, ...cambios(r)]));
    win.aviso(r.adjustments.length > 0 ? `Cierre registrado. Reubicaciones para el Jefe: ${r.adjustments.length}.` : "Cierre registrado.", "ok");
    vista = null;
    bloquear(confirmar, true);
    refrescar();
  }, "primary");
  bloquear(confirmar, true);

  const verPrevia = win.boton("Ver vista previa", "cierrePrevia", async () => {
    const r = respuestas(filas);
    const p = await acciones.cierrePrevia(r);
    pintar(previa, lineas(cambios(p), "Ninguna tarea cambia."), p.missed.length === 0 ? null : seccion("Bloques perdidos", lineas(p.missed.map((m) => `${m.span} · ${m.title}`))));
    vista = JSON.stringify(r);
    bloquear(confirmar, p.closed);
    win.aviso(p.closed ? "Este día ya está cerrado." : "Vista previa lista: nada se cambió todavía.", "info");
  });

  const enviar = win.boton("Enviar check-in", "checkin", async () => {
    const r = await acciones.checkin({ texto: mensaje.value, estado: estado.value, tarea: tarea.value || null, capturar: captura.value.trim() || null, ia: ia.input.checked });
    const paso = r.micro_step === null ? [] : [`Siguiente paso: ${r.micro_step.action} (${r.micro_step.minutes} min)`];
    pintar(respuesta, lineas([r.message, ...paso, `Registrado en ${r.path}`, ...(r.capture === null ? [] : [`Captura: ${r.capture}`])]));
    mensaje.value = "";
    captura.value = "";
    win.aviso(r.ai_fallback ? "Check-in guardado (sin IA: se usó la respuesta local)." : "Check-in guardado.", "ok");
    refrescar();
    await cargar();
  }, "primary");

  function invalidarPrevia(): void {
    vista = null;
    bloquear(confirmar, true);
    pintar(previa);
  }

  function pintarSinPlan(): void {
    pintar(titulo, h("div", { class: "lo-lead" }, h("span", { class: "lo-lead__text", text: "Sin plan para hoy" }), etiqueta({ texto: "sin plan", tono: "alerta" })));
    pintar(hoy, h("p", { class: "lo-empty", text: "Pide al Jefe que proponga la semana y apruébala." }));
    pintar(tarea, opcion("", "(ninguna)"));
    filas = [];
    pintarCierre();
  }

  function pintarCierre(): void {
    pintar(cierre, paginar(filas, formulario, { porPagina: 2, contenedor: "div", vacio: "Hoy no hay tareas en el plan." }));
    tabs.contador("cierre", filas.length);
    invalidarPrevia();
  }

  function pintarDia(b: Briefing): void {
    pintar(titulo, ...cabecera(b));
    pintar(
      hoy,
      paginar(b.blocks, (x) => fila({ titulo: x.title, detalle: x.span, etiquetas: [{ texto: x.kind, tono: TONO_BLOQUE[x.kind] }] }), { vacio: "Sin bloques." }),
      b.adjustments.length === 0 ? null : seccion("Ajustes para el Jefe", paginar(b.adjustments, (a) => fila({ titulo: a.text, detalle: a.at }), { porPagina: 3 })),
    );
    const tareas = tareasDeHoy(b);
    pintar(tarea, opcion("", "(ninguna)"), ...tareas.map(([id, nombre]) => opcion(id, nombre)));
    filas = tareas.map(([id, nombre]) => {
      const r: Respuesta = { tarea: id, titulo: nombre, tipo: selector(RESPUESTAS), avance: entrada("p. ej. 50"), restante: entrada("p. ej. 1h30m") };
      for (const el of [r.tipo, r.avance, r.restante]) el.addEventListener("input", invalidarPrevia);
      return r;
    });
    pintarCierre();
  }

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      try {
        pintarDia(await acciones.hoy());
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) pintarSinPlan();
        else throw err;
      }
    });
  }

  const tabs = pestanas("coach", [
    { id: "hoy", label: "Hoy", panel: [hoy] },
    {
      id: "checkin",
      label: "Check-in",
      panel: [
        h("div", { class: "lo-row" }, campo("¿Cómo vas?", estado), campo("Tarea del bloque", tarea)),
        campo("Mensaje", mensaje),
        campo("Captura", captura),
        h("div", { class: "lo-actions lo-actions--split" }, ia.el, enviar),
        respuesta,
      ],
    },
    {
      id: "cierre",
      label: "Cierre",
      panel: [h("p", { class: "lo-muted", text: "Solo cambia lo que respondas. Nada se aplica sin confirmar." }), cierre, h("div", { class: "lo-actions" }, verPrevia, confirmar), previa],
    },
  ]);

  win.body.append(titulo, tabs.el);
  // Si las respuestas cambian después de la vista previa, hay que volver a verla.
  win.el.addEventListener("change", () => {
    if (vista !== null && vista !== JSON.stringify(respuestas(filas))) invalidarPrevia();
  });
  return win;
}
