// Tu espacio: tareas (F11-T10). Captura rápida al Inbox, búsqueda con los filtros de la API y próximos
// vencimientos. Completar o avanzar una tarea se hace en el cierre del día (oficina del Coach): no hay otra ruta.
import type { Task } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones } from "../acciones";
import { campo, entrada, lista, pintar, seccion, selector, Ventana } from "../ventana";

const ESTADOS = [
  ["", "Todos"],
  ["propuesta", "Propuesta"],
  ["programada", "Programada"],
  ["confirmada", "Confirmada"],
  ["bloqueada", "Bloqueada"],
  ["completada", "Completada"],
  ["cancelada", "Cancelada"],
] as const;

const DIAS = [
  ["7", "7 días"],
  ["14", "14 días"],
  ["30", "30 días"],
] as const;

export function lineaTarea(t: Task): string {
  const partes = [t.title, t.status, t.area ?? "sin área", t.effort === null ? null : `esfuerzo ${t.effort}`, t.due === null ? null : `vence ${t.due}`, t.id];
  return partes.filter((p): p is string => p !== null).join(" · ");
}

export function crearTareas(refrescar: () => void): Ventana {
  const captura = entrada("Escribe una idea o tarea y pulsa Capturar");
  const area = entrada("p. ej., universidad");
  const estado = selector(ESTADOS);
  const buscar = entrada("Texto");
  const dias = selector(DIAS, "14");
  const resultados = h("div", { class: "lo-result" });
  const proximas = h("div", { class: "lo-result" });

  const win: Ventana = new Ventana("tareas", "Tu espacio · Tareas", { rutas: ["proximas"], onOpen: () => void cargarProximas() });

  async function cargarProximas(): Promise<void> {
    await win.ejecutar(null, async () => {
      pintar(proximas, lista((await acciones.proximas(Number(dias.value))).map(lineaTarea), "Nada vence en ese plazo."));
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

  win.body.append(
    seccion("Captura rápida", h("div", { class: "lo-row" }, captura, capturar)),
    seccion(
      "Buscar tareas",
      h("div", { class: "lo-row" }, campo("Área", area), campo("Estado", estado), campo("Texto", buscar)),
      h(
        "div",
        { class: "lo-actions" },
        win.boton("Buscar", "tareas", async () => {
          const t = await acciones.tareas({ area: area.value.trim(), estado: estado.value, texto: buscar.value.trim() });
          pintar(resultados, lista(t.map(lineaTarea), "Ninguna tarea coincide."));
        }),
      ),
      resultados,
    ),
    seccion("Próximos vencimientos", campo("Plazo", dias), h("div", { class: "lo-actions" }, win.boton("Actualizar", "proximas", cargarProximas)), proximas),
    h("p", { class: "lo-muted", text: "Para marcar una tarea como hecha o parcial, usa el cierre del día en la oficina del Coach." }),
  );
  return win;
}
