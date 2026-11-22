// Ventanas de consulta y revisión (F11-T09 y F11-T10): Propuestas (aceptar o rechazar), Historial,
// Indicadores, Calendario (solo lectura) y Ayuda. Los datos vienen de las rutas existentes, tal cual.
import type { ReviewItem } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones } from "../acciones";
import { campo, entrada, lista, pintar, seccion, selector, Ventana } from "../ventana";
import { momento } from "./formato";

function pct(v: number | null): string {
  return v === null ? "sin datos" : `${v} %`;
}

export function crearPropuestas(refrescar: () => void): Ventana {
  const cuerpo = h("div", {});
  const win: Ventana = new Ventana("propuestas", "Propuestas de los especialistas", { rutas: ["propuestas"], onOpen: () => void cargar() });

  function fila(p: ReviewItem): HTMLElement {
    const que = p.kind === "nueva_tarea" ? `Nueva tarea: ${p.title ?? "?"}` : `Prioridad de ${p.task_title ?? p.task_id ?? "?"}: ${p.current_priority ?? "?"} → ${p.priority}`;
    const decidir = (aceptar: boolean) => async () => {
      if (aceptar) await acciones.aceptar(p.ref);
      else await acciones.rechazar(p.ref);
      win.aviso(aceptar ? "Propuesta aceptada." : "Propuesta rechazada.", "ok");
      refrescar();
      await cargar();
    };
    return h(
      "article",
      { class: "lo-card" },
      h("h4", { text: que }),
      h("p", { class: "lo-muted", text: `${p.area ?? "sin área"} · ${p.reason}${p.due === null ? "" : ` · vence ${p.due}`}${p.effort === null ? "" : ` · ${p.effort}`}` }),
      h("div", { class: "lo-actions" }, win.boton("Aceptar", "aceptar", decidir(true), "primary"), win.boton("Rechazar", "rechazar", decidir(false), "danger")),
    );
  }

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const r = await acciones.propuestas();
      const pendientes = r.items.filter((p) => p.status === "pendiente");
      pintar(cuerpo, 
        ...(pendientes.length === 0 ? [h("p", { class: "lo-empty", text: "No hay propuestas pendientes." })] : pendientes.map(fila)),
        ...(r.skipped === undefined || r.skipped.length === 0 ? [] : [seccion("No se pudieron leer", lista(r.skipped))]),
      );
    });
  }

  win.body.append(h("p", { class: "lo-muted", text: "Los especialistas solo proponen: nada cambia en tus tareas hasta que aceptas." }), cuerpo);
  return win;
}

export function crearHistorial(): Ventana {
  const cuerpo = h("div", { class: "lo-result" });
  const win: Ventana = new Ventana("historial", "Historial", { rutas: ["historial"], onOpen: () => void cargar() });

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const r = await acciones.historial(200);
      const lineas = [...r.entries].reverse().map((e) => `${momento(e.ts)} · ${e.actor} · ${e.action.replaceAll("_", " ")} · ${e.target} · ${e.result}${e.reason === null ? "" : ` (${e.reason})`}`);
      pintar(cuerpo, 
        lista(lineas, "Sin actividad este mes ni el anterior."),
        r.truncated ? h("p", { class: "lo-muted", text: "Se muestran las 200 más recientes." }) : null,
        r.malformed > 0 ? h("p", { class: "lo-muted", text: `${r.malformed} línea(s) del registro no se pudieron leer.` }) : null,
      );
    });
  }

  win.body.append(h("div", { class: "lo-actions" }, win.boton("Actualizar", "historial", cargar)), cuerpo);
  return win;
}

export function crearIndicadores(): Ventana {
  const dias = selector([["7", "7 días"], ["28", "28 días"], ["90", "90 días"]], "28");
  const cuerpo = h("div", { class: "lo-result" });
  const win: Ventana = new Ventana("indicadores", "Indicadores", { rutas: ["indicadores"], onOpen: () => void cargar() });

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const i = await acciones.indicadores(Number(dias.value));
      const n = i.notifications;
      pintar(cuerpo, 
        h("p", { class: "lo-lead", text: `${i.start} → ${i.end} · racha de cierres: ${i.streak} día(s)` }),
        lista([
          `Entregas: ${i.deliveries.completed} de ${i.deliveries.due} (${pct(i.deliveries.percent)}), olvidadas ${i.deliveries.forgotten}`,
          `Adherencia al cierre: ${pct(i.adherence.percent)} (${i.adherence.closed_days} día(s) cerrados)`,
          `Uso: ${i.usage.active_days} día(s) activos, ${i.usage.days_per_week} por semana`,
          `Aceptación de planes: ${pct(i.acceptance.percent)} (${i.acceptance.approved} de ${i.acceptance.proposed})`,
          `Avisos: ${n.sent} enviados, ${n.failed} fallidos, máx. ${n.max_per_day} al día`,
        ]),
        i.costs.length === 0 ? null : seccion("Coste por ritual", lista(i.costs.map((c) => `${c.agent} · ${c.ritual}: ${c.calls} llamada(s), mediana ${c.latency_median_ms} ms, ~${c.cost_estimated_usd} USD`))),
        i.not_measured.length === 0 ? null : seccion("Sin medir", lista(i.not_measured.map((m) => `${m.name}: ${m.reason}`))),
      );
    });
  }

  dias.addEventListener("change", () => void cargar());
  win.body.append(h("div", { class: "lo-row" }, campo("Periodo", dias), win.boton("Actualizar", "indicadores", cargar)), cuerpo);
  return win;
}

export function crearCalendario(): Ventana {
  const estado = h("div", { class: "lo-result" });
  const semana = entrada("AAAA-Wnn (vacío = la actual)");
  const ocupado = h("div", { class: "lo-result" });
  const win: Ventana = new Ventana("calendario", "Calendario (solo lectura)", { rutas: ["calendario"], onOpen: () => void cargar() });

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const c = await acciones.calendario();
      pintar(estado, 
        h("p", { class: "lo-lead", text: c.configured ? (c.can_read ? "Google Calendar conectado para lectura." : "Configurado, pero sin permiso de lectura.") : "Google Calendar no está configurado." }),
        lista(c.grants.map((g) => `${g.grant}: ${g.connected ? "conectado" : "no conectado"}${g.problem === null ? "" : ` · ${g.problem}`}`)),
        c.problem === null ? null : h("p", { class: "lo-muted", text: c.problem }),
        h("p", { class: "lo-muted", text: "Conectar, publicar y revocar se hacen en la CLI (lifeos calendario), por seguridad." }),
      );
    });
  }

  win.body.append(
    estado,
    seccion(
      "Disponibilidad de la semana",
      h("div", { class: "lo-row" }, campo("Semana", semana)),
      h(
        "div",
        { class: "lo-actions" },
        win.boton("Consultar", "disponibilidad", async () => {
          const d = await acciones.disponibilidad(semana.value);
          pintar(ocupado, 
            h("p", { class: "lo-muted", text: `Semana ${d.week} · plan ${d.plan_status ?? "sin plan"}` }),
            seccion("Ocupado", lista(d.busy.map((b) => `${b.day} ${b.span} · ${b.source} (${b.confidence})`), "Nada ocupado.")),
            seccion("Choques con el plan", lista(d.clashes.map((c) => `${c.block.day} ${c.block.span} · ${c.block.title} choca con ${c.busy.span}`), "Sin choques.")),
          );
        }),
      ),
      ocupado,
    ),
  );
  return win;
}

export function crearAyuda(): Ventana {
  const win = new Ventana("ayuda", "Ayuda");
  win.body.append(
    h("p", { class: "lo-lead", text: "El edificio es la cara visual de LifeOS: todo lo que ves sale de tu bóveda y cada botón usa la misma API que la vista clásica." }),
    seccion(
      "Salas",
      lista([
        "Tu espacio: tus tareas, la captura rápida y los vencimientos.",
        "Oficina del Jefe: proponer, aprobar y replanificar la semana.",
        "Oficina del Coach: el día de hoy, el check-in y el cierre.",
        "Una sala por especialista: probar, ejecutar, ciclo de vida y exportación.",
      ]),
    ),
    seccion("Atajos", lista(["O: vista general", "[ y ]: panel lateral y actividad", "Esc: cerrar la ventana", "Clic en un agente o sala: abre su oficina"])),
    seccion("Solo en la CLI", lista(["Respaldo de la bóveda", "Conectar, publicar y revocar Google Calendar", "Servicio de rituales y Telegram"])),
  );
  return win;
}
