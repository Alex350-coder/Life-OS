// Tu espacio: tareas (F11-T10). Captura rápida al Inbox, búsqueda con los filtros de la API y próximos
// vencimientos. Completar o avanzar una tarea se hace en el cierre del día (oficina del Coach): no hay otra ruta.
import type { Task } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones } from "../acciones";
import { fila, paginar, pestanas, type Fila } from "../componentes";
import { campo, entrada, pintar, selector, Ventana } from "../ventana";
import { dia, tonoEstado } from "./formato";

const ESTADOS = [
  ["", "Todos"],
  ["propuesta", "Propuesta"],
  ["programada", "Programada"],
  ["confirmada", "Confirmada"],
  ["bloqueada", "Bloqueada"],
  ["completada", "Completada"],
  ["cancelada", "Cancelada"],
] as const;

const AREAS = [
  ["", "Todas"],
  ["universidad", "Universidad"],
  ["proyectos", "Proyectos"],
  ["carrera", "Carrera"],
  ["hobbies", "Hobbies"],
  ["salud", "Salud"],
  ["finanzas", "Finanzas"],
  ["cotidiano", "Cotidiano"],
] as const;

const DIAS = [
  ["7", "7 días"],
  ["14", "14 días"],
  ["30", "30 días"],
] as const;

export function filaTarea(t: Task): Fila {
  const detalle = [t.area, t.due === null ? null : `vence ${dia(t.due)}`, t.effort === null ? null : `esfuerzo ${t.effort}`, t.id];
  return { titulo: t.title, etiquetas: [{ texto: t.status, tono: tonoEstado(t.status) }], detalle: detalle.filter((x): x is string => x !== null).join(" · ") };
}

function listaTareas(tareas: readonly Task[], vacio: string): HTMLElement {
  return paginar(tareas, (t) => fila(filaTarea(t)), { porPagina: 4, vacio });
}

export function crearTareas(refrescar: () => void): Ventana {
  const captura = entrada("Escribe una idea o tarea y pulsa Capturar");
  const area = selector(AREAS);
  const estado = selector(ESTADOS);
  const buscar = entrada("Texto");
  const dias = selector(DIAS, "14");
  const resultados = h("div", { class: "lo-result" });
  const proximas = h("div", { class: "lo-result" });

  const win: Ventana = new Ventana("tareas", "Tu espacio · Tareas", { rutas: ["proximas"], onOpen: () => void cargarProximas() });

  async function cargarProximas(): Promise<void> {
    await win.ejecutar(null, async () => {
      const t = await acciones.proximas(Number(dias.value));
      pintar(proximas, listaTareas(t, "Nada vence en ese plazo."));
      tabs.contador("proximas", t.length);
    });
  }

  const capturar = win.boton("Capturar", "capturar", async () => {
    const valor = captura.value.trim();
    if (valor === "") {
      win.aviso("Escribe algo para capturar.", "error");
      return;
    }
    const r = await acciones.capturar(valor);
    captura.value = "";
    win.aviso(`Capturado en ${r.path}.`, "ok");
    refrescar();
  }, "primary");
  captura.addEventListener("keydown", (e) => {
    if (e.key === "Enter") capturar.click();
  });
  dias.addEventListener("change", () => void cargarProximas());

  const buscarBtn = win.boton("Buscar", "tareas", async () => {
    const t = await acciones.tareas({ area: area.value, estado: estado.value, texto: buscar.value.trim() });
    pintar(resultados, listaTareas(t, "Ninguna tarea coincide."));
    tabs.contador("buscar", t.length);
  });
  buscar.addEventListener("keydown", (e) => {
    if (e.key === "Enter") buscarBtn.click();
  });

  const tabs = pestanas("tareas", [
    { id: "proximas", label: "Próximas", panel: [h("div", { class: "lo-row" }, campo("Plazo", dias), win.boton("Actualizar", "proximas", cargarProximas)), proximas] },
    { id: "buscar", label: "Buscar", panel: [h("div", { class: "lo-row" }, campo("Área", area), campo("Estado", estado), campo("Texto", buscar), buscarBtn), resultados] },
  ]);

  win.body.append(
    h("div", { class: "lo-row" }, campo("Captura rápida", captura), capturar),
    tabs.el,
    h("p", { class: "lo-muted", text: "Para marcar una tarea como hecha o parcial, usa el cierre del día en la oficina del Coach." }),
  );
  return win;
}
