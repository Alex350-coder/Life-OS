// Oficina del Jefe (F11-T07): la semana con su carga y el plan. Proponer, aprobar y replanificar llaman a las
// rutas de siempre; el Jefe valida en el servidor y nada queda aprobado hasta que pulsas «Aprobar».
import type { DayLoad, PlanBlock, PlanResult, Week } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { createProgress, updateProgress } from "../../habblaud/ui/widgets";
import { acciones } from "../acciones";
import { desplegable, etiqueta, fila, paginar, pestanas, type Fila } from "../componentes";
import { campo, casilla, entrada, pintar, Ventana } from "../ventana";
import { dia, horas, TONO_BLOQUE } from "./formato";

const DIAS_POR_PAGINA = 7;

function cabecera(w: Week): HTMLElement {
  return h(
    "div",
    { class: "lo-lead" },
    h("span", { class: "lo-lead__text", text: `Semana ${w.week}` }),
    etiqueta(w.status === null ? { texto: "sin plan", tono: "alerta" } : { texto: w.status, tono: w.status === "aprobado" ? "ok" : "info" }),
    w.revision > 0 ? etiqueta({ texto: `revisión ${w.revision}` }) : null,
  );
}

function cargaDia(d: DayLoad): HTMLElement {
  const barra = createProgress(`Carga del ${d.day}`);
  updateProgress(barra, Math.min(d.planned_minutes, d.budget_minutes), d.budget_minutes);
  return h(
    "li",
    { class: "lo-item lo-load" },
    h(
      "div",
      { class: "lo-item__head" },
      h("span", { class: "lo-item__title", text: dia(d.day) }),
      d.over_budget ? etiqueta({ texto: "sobre el límite", tono: "error" }) : null,
      h("span", { class: "lo-load__extra", text: `fijo ${horas(d.fixed_minutes)} · protegido ${horas(d.protected_minutes)}` }),
      h("span", { class: "lo-load__total", text: `${horas(d.planned_minutes)} de ${horas(d.budget_minutes)}` }),
    ),
    barra,
  );
}

function bloque(b: PlanBlock): Fila {
  return { titulo: b.title, detalle: `${dia(b.day)} · ${b.span}`, etiquetas: [{ texto: b.kind, tono: TONO_BLOQUE[b.kind] }] };
}

/** Lo que pide atención primero: conflictos, alertas y lo que no cupo; después, el trabajo propuesto. */
function filasPlan(r: PlanResult): Fila[] {
  const p = r.proposal;
  return [
    ...r.conflicts.map((c): Fila => ({ titulo: c.message, etiquetas: [{ texto: "conflicto", tono: "error" }] })),
    ...p.alerts.map((a): Fila => ({ titulo: a, etiquetas: [{ texto: "alerta", tono: "alerta" }] })),
    ...p.unplanned.map((u): Fila => ({ titulo: `${u.title} (${horas(u.minutes)})`, detalle: `${u.reason}. ${u.suggestion}`, etiquetas: [{ texto: "sin hueco", tono: "alerta" }] })),
    ...p.work.map(bloque),
  ];
}

function planResumen(r: PlanResult): HTMLElement {
  return h(
    "div",
    { class: "lo-result" },
    h("div", { class: "lo-lead" }, h("span", { class: "lo-lead__text", text: r.accepted ? "Aceptado" : "No aceptado" }), etiqueta({ texto: r.status ?? "sin estado", tono: r.accepted ? "ok" : "error" })),
    r.path === null ? null : h("p", { class: "lo-muted", text: r.path }),
    paginar(filasPlan(r), fila, { porPagina: 4, vacio: "Sin bloques de trabajo." }),
  );
}

export function crearJefe(refrescar: () => void): Ventana {
  const semana = entrada("AAAA-Wnn (vacío = la actual)");
  const ia = casilla("Proponer con IA");
  const titulo = h("div", { class: "lo-result" });
  const motivo = h("div", { class: "lo-result" });
  const resultado = h("div", { class: "lo-result" });
  const carga = h("div", { class: "lo-result" });
  const bloques = h("div", { class: "lo-result" });

  const win: Ventana = new Ventana("jefe", "Oficina del Jefe", { rutas: ["semana"], onOpen: () => void cargar() });

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const w = await acciones.semana(semana.value);
      pintar(titulo, cabecera(w));
      pintar(motivo, w.justification === "" ? null : desplegable("Por qué este plan", h("p", { class: "lo-muted", text: w.justification })));
      pintar(carga, paginar(w.load, cargaDia, { porPagina: DIAS_POR_PAGINA, vacio: "Sin carga planificada." }));
      pintar(bloques, paginar(w.blocks, (b) => fila(bloque(b)), { vacio: "Sin bloques." }));
      tabs.contador("bloques", w.blocks.length);
    });
  }

  async function plan(run: () => Promise<PlanResult>, hecho: string): Promise<void> {
    const r = await run();
    pintar(resultado, planResumen(r));
    win.aviso(hecho, r.accepted ? "ok" : "error");
    refrescar();
    await cargar();
  }

  const tabs = pestanas("jefe", [
    {
      id: "plan",
      label: "Plan",
      panel: [
        h("div", { class: "lo-row" }, campo("Semana", semana), ia.el),
        h(
          "div",
          { class: "lo-actions" },
          win.boton("Proponer la semana", "proponer", () => plan(() => acciones.proponer(semana.value, ia.input.checked), "Plan propuesto. Revísalo y apruébalo."), "primary"),
          win.boton("Aprobar el plan propuesto", "aprobar", () => plan(() => acciones.aprobar(semana.value), "Plan aprobado y publicado en las notas diarias.")),
          win.boton("Replanificar desde ahora", "replanificar", () => plan(() => acciones.replanificar(semana.value, ia.input.checked), "Replanificado. Revisa y aprueba la nueva versión.")),
          win.boton("Ver la semana", "semana", cargar),
        ),
        motivo,
        resultado,
      ],
    },
    { id: "carga", label: "Carga", panel: [carga] },
    { id: "bloques", label: "Bloques", panel: [bloques] },
  ]);

  win.body.append(titulo, tabs.el);
  return win;
}
