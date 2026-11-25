// Ventanas de consulta y revisión (F11-T09 y F11-T10): Propuestas (aceptar o rechazar), Historial,
// Indicadores, Calendario (solo lectura) y Ayuda. Los datos vienen de las rutas existentes, tal cual.
import type { Indicators, ReviewItem } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones } from "../acciones";
import { cifra, datos, desplegable, etiqueta, fila, lineas, paginar, pestanas, type EstadoPagina, type Etiqueta } from "../componentes";
import { campo, entrada, pintar, selector, Ventana } from "../ventana";
import { dia, momento, tonoEstado } from "./formato";

const PERIODOS = [
  ["7", "7 días"],
  ["28", "28 días"],
  ["90", "90 días"],
] as const;

function pct(v: number | null): string {
  return v === null ? "sin datos" : `${v} %`;
}

export function crearPropuestas(refrescar: () => void): Ventana {
  const cuerpo = h("div", { class: "lo-result" });
  const pagina: EstadoPagina = { pagina: 0 };
  const win: Ventana = new Ventana("propuestas", "Propuestas de los especialistas", { rutas: ["propuestas"], onOpen: () => void cargar() });

  function tarjeta(p: ReviewItem): HTMLElement {
    const nueva = p.kind === "nueva_tarea";
    const etiquetas: Etiqueta[] = [{ texto: nueva ? "nueva tarea" : "prioridad", tono: "info" }, { texto: nueva ? p.priority : `${p.current_priority ?? "?"} → ${p.priority}`, tono: "alerta" }];
    if (p.area !== null) etiquetas.push({ texto: p.area });
    const extra = [p.due === null ? null : `vence ${dia(p.due)}`, p.effort === null ? null : `esfuerzo ${p.effort}`].filter((x): x is string => x !== null);
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
      h("div", { class: "lo-card__head" }, h("h4", { class: "lo-card__title", text: (nueva ? p.title : p.task_title ?? p.task_id) ?? "?" }), ...etiquetas.map(etiqueta)),
      h("p", { class: "lo-muted", text: [p.reason, ...extra].join(" · ") }),
      h("div", { class: "lo-actions" }, win.boton("Aceptar", "aceptar", decidir(true), "primary"), win.boton("Rechazar", "rechazar", decidir(false), "danger")),
    );
  }

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const r = await acciones.propuestas();
      const pendientes = r.items.filter((p) => p.status === "pendiente");
      const ilegibles = r.skipped ?? [];
      pintar(
        cuerpo,
        paginar(pendientes, tarjeta, { porPagina: 2, contenedor: "div", estado: pagina, vacio: "No hay propuestas pendientes." }),
        ilegibles.length === 0 ? null : desplegable(`No se pudieron leer (${ilegibles.length})`, lineas(ilegibles)),
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
      const notas = [r.truncated ? "Se muestran las 200 más recientes." : null, r.malformed > 0 ? `${r.malformed} línea(s) del registro no se pudieron leer.` : null].filter((x): x is string => x !== null);
      pintar(
        cuerpo,
        paginar(
          [...r.entries].reverse(),
          (e) =>
            fila({
              titulo: e.action.replaceAll("_", " "),
              detalle: `${momento(e.ts)} · ${e.actor} · ${e.target}${e.reason === null ? "" : ` (${e.reason})`}`,
              etiquetas: [{ texto: e.result, tono: tonoEstado(e.result) }],
            }),
          { porPagina: 6, vacio: "Sin actividad este mes ni el anterior." },
        ),
        notas.length === 0 ? null : h("p", { class: "lo-muted", text: notas.join(" ") }),
      );
    });
  }

  win.body.append(cuerpo, h("div", { class: "lo-actions" }, win.boton("Actualizar", "historial", cargar)));
  return win;
}

function resumen(i: Indicators): HTMLElement {
  const n = i.notifications;
  return h(
    "div",
    { class: "lo-stats" },
    cifra("Racha de cierres", `${i.streak} día(s)`),
    cifra("Entregas", pct(i.deliveries.percent), `${i.deliveries.completed} de ${i.deliveries.due}, olvidadas ${i.deliveries.forgotten}`),
    cifra("Adherencia al cierre", pct(i.adherence.percent), `${i.adherence.closed_days} día(s) cerrados`),
    cifra("Aceptación de planes", pct(i.acceptance.percent), `${i.acceptance.approved} de ${i.acceptance.proposed}`),
    cifra("Uso", `${i.usage.active_days} día(s)`, `${i.usage.days_per_week} por semana`),
    cifra("Avisos", `${n.sent} enviados`, `${n.failed} fallidos, máx. ${n.max_per_day} al día`),
  );
}

export function crearIndicadores(): Ventana {
  const dias = selector(PERIODOS, "28");
  const rango = h("div", { class: "lo-result" });
  const general = h("div", { class: "lo-result" });
  const costes = h("div", { class: "lo-result" });
  const sinMedir = h("div", { class: "lo-result" });
  const win: Ventana = new Ventana("indicadores", "Indicadores", { rutas: ["indicadores"], onOpen: () => void cargar() });

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const i = await acciones.indicadores(Number(dias.value));
      pintar(rango, h("p", { class: "lo-muted", text: `${dia(i.start)} → ${dia(i.end)}` }));
      pintar(general, resumen(i));
      pintar(costes, paginar(i.costs, (c) => fila({ titulo: `${c.agent} · ${c.ritual}`, detalle: `${c.calls} llamada(s) · mediana ${c.latency_median_ms} ms · ~${c.cost_estimated_usd} USD` }), { vacio: "Sin llamadas a modelos en el periodo." }));
      pintar(sinMedir, paginar(i.not_measured, (m) => fila({ titulo: m.name, detalle: m.reason }), { porPagina: 4, vacio: "Todo se mide." }));
    });
  }

  const tabs = pestanas("indicadores", [
    { id: "resumen", label: "Resumen", panel: [general] },
    { id: "costes", label: "Costes", panel: [costes] },
    { id: "sin-medir", label: "Sin medir", panel: [sinMedir] },
  ]);

  dias.addEventListener("change", () => void cargar());
  win.body.append(h("div", { class: "lo-row" }, campo("Periodo", dias), win.boton("Actualizar", "indicadores", cargar)), rango, tabs.el);
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
      pintar(
        estado,
        h("p", { class: "lo-lead", text: c.configured ? (c.can_read ? "Google Calendar conectado para lectura." : "Configurado, pero sin permiso de lectura.") : "Google Calendar no está configurado." }),
        paginar(c.grants, (g) => fila({ titulo: g.grant, etiquetas: [{ texto: g.connected ? "conectado" : "no conectado", tono: g.connected ? "ok" : "neutro" }], ...(g.problem === null ? {} : { detalle: g.problem }) })),
        c.problem === null ? null : h("p", { class: "lo-muted", text: c.problem }),
        h("p", { class: "lo-muted", text: "Conectar, publicar y revocar se hacen en la CLI (lifeos calendario), por seguridad." }),
      );
    });
  }

  const consultar = win.boton("Consultar", "disponibilidad", async () => {
    const d = await acciones.disponibilidad(semana.value);
    pintar(
      ocupado,
      h("div", { class: "lo-lead" }, h("span", { class: "lo-lead__text", text: `Semana ${d.week}` }), etiqueta({ texto: d.plan_status ?? "sin plan" })),
      paginar(d.clashes, (c) => fila({ titulo: c.block.title, detalle: `${dia(c.block.day)} ${c.block.span} choca con ${c.busy.span}`, etiquetas: [{ texto: "choque", tono: "error" }] }), { porPagina: 2, vacio: "Sin choques." }),
      paginar(d.busy, (b) => fila({ titulo: `${dia(b.day)} · ${b.span}`, detalle: b.source, etiquetas: [{ texto: `confianza ${b.confidence}` }] }), { porPagina: 4, vacio: "Nada ocupado." }),
    );
  });

  const tabs = pestanas("calendario", [
    { id: "estado", label: "Estado", panel: [estado] },
    { id: "disponibilidad", label: "Disponibilidad", panel: [h("div", { class: "lo-row" }, campo("Semana", semana), consultar), ocupado] },
  ]);
  win.body.append(tabs.el);
  return win;
}

export function crearAyuda(): Ventana {
  const win = new Ventana("ayuda", "Ayuda");
  const tabs = pestanas("ayuda", [
    {
      id: "salas",
      label: "Salas",
      panel: [
        datos([
          ["Tu espacio", "Tus tareas, la captura rápida y los vencimientos."],
          ["Oficina del Jefe", "Proponer, aprobar y replanificar la semana."],
          ["Oficina del Coach", "El día de hoy, el check-in y el cierre."],
          ["Especialistas", "Probar, ejecutar, ciclo de vida y exportación."],
        ]),
      ],
    },
    {
      id: "atajos",
      label: "Atajos",
      panel: [
        datos([
          ["O", "Vista general"],
          ["[ y ]", "Panel lateral y actividad"],
          ["Esc", "Cerrar la ventana"],
          ["Clic", "En un agente o sala: abre su oficina"],
          ["← y →", "Cambiar de pestaña"],
        ]),
      ],
    },
    { id: "cli", label: "Solo en la CLI", panel: [lineas(["Respaldo de la bóveda", "Conectar, publicar y revocar Google Calendar", "Servicio de rituales y Telegram"])] },
  ]);
  win.body.append(h("p", { class: "lo-muted", text: "Todo lo que ves sale de tu bóveda y cada botón usa la API local, que valida y aplica los permisos." }), tabs.el);
  return win;
}
