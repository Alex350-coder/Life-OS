// Agentes y especialistas (F11-T09): permisos efectivos, uso del mes, importación, exportación, prueba,
// ejecución y ciclo de vida. Cada botón llama a su ruta; el registro valida y el servidor decide.
// Se ve un especialista cada vez; sus permisos y opciones se despliegan si hacen falta.
import type { Agent, Agents, ImportedAgent, RunResult } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones, type LifecycleAction } from "../acciones";
import { datos, desplegable, etiqueta, fila, paginar, pestanas, type EstadoPagina, type Fila } from "../componentes";
import { campo, casilla, entrada, pintar, texto, Ventana } from "../ventana";
import { tonoEstado } from "./formato";

const CICLO: readonly [LifecycleAction, string][] = [
  ["aprobar", "Aprobar"],
  ["activar", "Activar"],
  ["desactivar", "Desactivar"],
  ["restaurar", "Restaurar"],
];

function lineaUso(calls: number, errors: number, coste: string): string {
  return `${calls} llamada(s), ${errors} error(es), ~${coste} USD`;
}

function tarjetaNucleo(a: Agent): HTMLElement {
  const escribe = a.writes.map((w) => `${w.root}${w.section === null ? "" : `#${w.section}`} (${w.mode})`).join(", ") || "nada";
  return h(
    "article",
    { class: "lo-card" },
    h("div", { class: "lo-card__head" }, h("h4", { class: "lo-card__title", text: a.name }), etiqueta({ texto: a.status, tono: tonoEstado(a.status) })),
    h("p", { class: "lo-muted", text: a.purpose }),
    desplegable(
      "Modelo, permisos y uso",
      datos([
        ["Modelo", `${a.provider}/${a.model}`],
        ["Lee", a.reads.join(", ") || "nada"],
        ["Escribe", escribe],
        ["Uso del mes", a.usage === null ? "sin uso" : lineaUso(a.usage.calls, a.usage.errors, a.usage.cost_estimated_usd)],
      ]),
    ),
  );
}

function filasEjecucion(r: RunResult): Fila[] {
  return [
    { titulo: r.dry_run ? "Prueba" : "Ejecución", detalle: `Semana ${r.week} · origen ${r.source}${r.path === null ? "" : ` · ${r.path}`}` },
    ...r.alerts.map((a): Fila => ({ titulo: a, etiquetas: [{ texto: "alerta", tono: "alerta" }] })),
    ...r.items.map((i): Fila => ({ titulo: i.title ?? i.task_id ?? "?", detalle: i.reason, etiquetas: [{ texto: i.kind === "nueva_tarea" ? "nueva" : "prioridad", tono: "info" }, { texto: i.priority }] })),
  ];
}

/** Descarga el YAML exportado como archivo (Blob local; nada sale del equipo). */
function descargar(nombre: string, contenido: string): void {
  const url = URL.createObjectURL(new Blob([contenido], { type: "text/yaml" }));
  const a = h("a", { attrs: { href: url, download: `${nombre}.yaml` } });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

export function crearAgentes(refrescar: () => void): Ventana & { enfocar(id: string): void } {
  const nucleo = h("div", { class: "lo-result" });
  const uso = h("div", { class: "lo-result" });
  const especialistas = h("div", { class: "lo-result" });
  const nombre = entrada("nombre-del-archivo (letras, números, . _ -)");
  const contenido = texto(6, "Pega aquí el YAML del agente");
  const archivo = h("input", { class: "lo-input lo-file", type: "file", attrs: { accept: ".yaml,.yml,.md,.txt" } });
  const semana = entrada("AAAA-Wnn (vacío = la actual)");
  const ia = casilla("Ejecutar con IA");
  const opciones = desplegable("Opciones de ejecución", h("div", { class: "lo-row" }, campo("Semana", semana), ia.el));
  const pagina: EstadoPagina = { pagina: 0 };
  const salidas = new Map<string, HTMLElement>(); // lo último que respondió cada especialista, para que no se pierda al recargar
  let pedido: string | null = null; // especialista que hay que enseñar cuando lleguen los datos

  const win = new Ventana("agentes", "Agentes y especialistas", { rutas: ["agentes", "uso"], onOpen: () => void cargar() }) as Ventana & { enfocar(id: string): void };

  archivo.addEventListener("change", () => {
    const f = archivo.files?.[0];
    if (f === undefined) return;
    void f.text().then((t) => {
      contenido.value = t;
      if (nombre.value.trim() === "") nombre.value = f.name.replace(/\.[^.]+$/, "");
    });
  });

  function salidaDe(id: string): HTMLElement {
    const el = salidas.get(id) ?? h("div", { class: "lo-result" });
    salidas.set(id, el);
    return el;
  }

  function tarjeta(s: ImportedAgent): HTMLElement {
    const salida = salidaDe(s.id);
    const mostrar = (r: RunResult) => pintar(salida, paginar(filasEjecucion(r), fila, { porPagina: 3 }));
    const ciclo = CICLO.map(([accion, label]) =>
      win.boton(label, "estado", async () => {
        await acciones.estado(s.id, accion);
        win.aviso(`${s.name}: ${label.toLowerCase()} hecho.`, "ok");
        refrescar();
        await cargar();
      }),
    );
    return h(
      "article",
      { class: "lo-card", attrs: { "data-agente": s.id } },
      h("div", { class: "lo-card__head" }, h("h4", { class: "lo-card__title", text: s.name }), etiqueta({ texto: s.state, tono: tonoEstado(s.state) }), etiqueta({ texto: `v${s.current}` }), s.area === null ? null : etiqueta({ texto: s.area })),
      h("p", { class: "lo-muted", text: s.purpose }),
      h(
        "div",
        { class: "lo-actions" },
        win.boton("Ejecutar", "ejecutar", async () => {
          mostrar(await acciones.ejecutar(s.id, semana.value, ia.input.checked));
          win.aviso(`${s.name}: propuestas escritas. Revísalas en Propuestas.`, "ok");
          refrescar();
        }, "primary"),
        win.boton("Probar", "probar", async () => {
          mostrar(await acciones.probar(s.id, ia.input.checked));
          win.aviso(`${s.name}: prueba registrada (no escribió propuestas).`, "ok");
          await cargar();
        }),
        win.boton("Exportar", "exportar", async () => {
          const r = await acciones.exportar(s.id);
          const yaml = texto(5);
          yaml.readOnly = true;
          yaml.value = r.contenido;
          pintar(salida, campo(`YAML de la versión ${r.version}`, yaml));
          descargar(`${s.id}-v${r.version}`, r.contenido);
          win.aviso(`${s.name}: versión ${r.version} exportada.`, "ok");
        }),
      ),
      salida,
      desplegable("Ciclo de vida", h("div", { class: "lo-actions" }, ...ciclo)),
      s.effective === null ? null : desplegable("Permisos", datos([["Lee", s.effective.reads.join(", ") || "nada"], ["Escribe", s.effective.writes.map((w) => w.root).join(", ") || "nada"]])),
      s.problems.length === 0 ? null : desplegable(`Problemas (${s.problems.length})`, paginar(s.problems, (p) => fila({ titulo: p, etiquetas: [{ texto: "problema", tono: "error" }] }), { porPagina: 3 })),
    );
  }

  function mostrarAgentes(a: Agents): void {
    const imp = a.imported ?? [];
    const i = pedido === null ? -1 : imp.findIndex((s) => s.id === pedido);
    if (i >= 0) pagina.pagina = i;
    pedido = null;
    pintar(
      nucleo,
      paginar(a.agents, tarjetaNucleo, { porPagina: 2, contenedor: "div", vacio: "Sin agentes." }),
      desplegable("Reglas comunes", datos([["Dueño del plan", a.plan_owner], ["Nadie escribe en", a.forbidden_write.join(", ") || "—"]])),
    );
    pintar(
      especialistas,
      paginar(imp, tarjeta, { porPagina: 1, contenedor: "div", estado: pagina, nombre: (s) => s.name, vacio: "Aún no hay especialistas. Impórtalos en la pestaña Importar." }),
      imp.length === 0 ? null : opciones,
    );
    tabs.contador("especialistas", imp.length);
  }

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const [a, u] = await Promise.all([acciones.agentes(), acciones.uso()]);
      mostrarAgentes(a);
      pintar(
        uso,
        h("p", { class: "lo-muted", text: `Total ${u.month}: ${lineaUso(u.total.calls, u.total.errors, u.total.cost_estimated_usd)}` }),
        paginar(u.rows, (r) => fila({ titulo: `${r.agent} · ${r.ritual}`, detalle: lineaUso(r.calls, r.errors, r.cost_estimated_usd) }), { vacio: "Sin llamadas a modelos este mes." }),
      );
    });
  }

  const tabs = pestanas("agentes", [
    { id: "especialistas", label: "Especialistas", panel: [especialistas] },
    { id: "nucleo", label: "Núcleo", panel: [nucleo] },
    { id: "uso", label: "Uso", panel: [uso] },
    {
      id: "importar",
      label: "Importar",
      panel: [
        h("div", { class: "lo-row" }, campo("Nombre", nombre), campo("Archivo", archivo)),
        campo("Contenido", contenido),
        h(
          "div",
          { class: "lo-actions" },
          win.boton("Importar", "importar", async () => {
            const a = await acciones.importar(nombre.value.trim(), contenido.value);
            win.aviso(`Importado ${a.name} (${a.state}). Pruébalo y apruébalo para activarlo.`, "ok");
            nombre.value = "";
            contenido.value = "";
            refrescar();
            pedido = a.id;
            await cargar();
            tabs.mostrar("especialistas");
          }, "primary"),
        ),
      ],
    },
  ]);

  win.enfocar = (id: string) => {
    pedido = id;
    tabs.mostrar("especialistas");
    win.open();
  };

  win.body.append(tabs.el);
  return win;
}
