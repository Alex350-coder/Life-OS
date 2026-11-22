// Oficina del Jefe (F11-T07): la semana con su carga y el plan. Proponer, aprobar y replanificar llaman a las
// rutas de siempre; el Jefe valida en el servidor y nada queda aprobado hasta que pulsas «Aprobar».
import type { PlanResult, Week } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones } from "../acciones";
import { campo, casilla, entrada, lista, pintar, seccion, Ventana } from "../ventana";
import { horas } from "./formato";

function semanaResumen(w: Week): HTMLElement {
  const estado = w.status === null ? "sin plan" : w.status;
  const carga = w.load.map(
    (d) => `${d.day}: ${horas(d.planned_minutes)} de ${horas(d.budget_minutes)} · fijo ${horas(d.fixed_minutes)} · protegido ${horas(d.protected_minutes)}${d.over_budget ? " · ¡sobre el límite!" : ""}`,
  );
  const bloques = w.blocks.map((b) => `${b.day} ${b.span} · ${b.title} (${b.kind})${b.task_id === null ? "" : ` · ${b.task_id}`}`);
  return h(
    "div",
    {},
    h("p", { class: "lo-lead", text: `Semana ${w.week} · ${estado}${w.revision > 0 ? ` · revisión ${w.revision}` : ""}` }),
    w.justification === "" ? null : h("p", { class: "lo-muted", text: w.justification }),
    seccion("Carga por día", lista(carga, "Sin carga planificada.")),
    seccion("Bloques", lista(bloques, "Sin bloques.")),
  );
}

function planResumen(r: PlanResult): HTMLElement {
  const p = r.proposal;
  return h(
    "div",
    {},
    h("p", { class: "lo-lead", text: `${r.accepted ? "Aceptado" : "No aceptado"} · ${r.status ?? "sin estado"}${r.path === null ? "" : ` · ${r.path}`}` }),
    seccion("Trabajo propuesto", lista(p.work.map((b) => `${b.day} ${b.span} · ${b.title}`), "Sin bloques de trabajo.")),
    p.unplanned.length === 0 ? null : seccion("Sin hueco", lista(p.unplanned.map((u) => `${u.title} (${horas(u.minutes)}): ${u.reason}. ${u.suggestion}`))),
    p.alerts.length === 0 ? null : seccion("Alertas", lista(p.alerts)),
    r.conflicts.length === 0 ? null : seccion("Conflictos del validador", lista(r.conflicts.map((c) => c.message))),
  );
}

export function crearJefe(refrescar: () => void): Ventana {
  const semana = entrada("AAAA-Wnn (vacío = la actual)");
  const ia = casilla("Proponer con IA");
  const resultado = h("div", { class: "lo-result" });
  const vista = h("div", { class: "lo-result" });

  const win: Ventana = new Ventana("jefe", "Oficina del Jefe", { rutas: ["semana"], onOpen: () => void cargar() });

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      pintar(vista, semanaResumen(await acciones.semana(semana.value)));
    });
  }

  async function plan(run: () => Promise<PlanResult>, hecho: string): Promise<void> {
    const r = await run();
    pintar(resultado, planResumen(r));
    win.aviso(hecho, r.accepted ? "ok" : "error");
    refrescar();
    await cargar();
  }

  win.body.append(
    h("p", { class: "lo-muted", text: "El Jefe propone y valida; nada queda aprobado hasta que tú lo apruebas." }),
    h("div", { class: "lo-row" }, campo("Semana", semana), ia.el),
    h(
      "div",
      { class: "lo-actions" },
      win.boton("Proponer la semana", "proponer", () => plan(() => acciones.proponer(semana.value, ia.input.checked), "Plan propuesto. Revísalo y apruébalo."), "primary"),
      win.boton("Aprobar el plan propuesto", "aprobar", () => plan(() => acciones.aprobar(semana.value), "Plan aprobado y publicado en las notas diarias.")),
      win.boton("Replanificar desde ahora", "replanificar", () => plan(() => acciones.replanificar(semana.value, ia.input.checked), "Replanificado. Revisa y aprueba la nueva versión.")),
      win.boton("Ver la semana", "semana", cargar),
    ),
    resultado,
    vista,
  );
  return win;
}
