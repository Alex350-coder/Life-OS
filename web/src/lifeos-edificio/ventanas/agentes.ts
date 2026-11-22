// Agentes y especialistas (F11-T09): permisos efectivos, uso del mes, importación, exportación, prueba,
// ejecución y ciclo de vida. Cada botón llama a su ruta; el registro valida y el servidor decide.
import type { Agent, Agents, ImportedAgent, RunResult } from "../../api/types";
import { h } from "../../habblaud/ui/dom";
import { acciones, type LifecycleAction } from "../acciones";
import { campo, casilla, entrada, lista, pintar, seccion, texto, Ventana } from "../ventana";

const CICLO: readonly [LifecycleAction, string][] = [
  ["aprobar", "Aprobar"],
  ["activar", "Activar"],
  ["desactivar", "Desactivar"],
  ["restaurar", "Restaurar"],
];

function lineaAgente(a: Agent): string {
  const uso = a.usage === null ? "sin uso este mes" : `${a.usage.calls} llamada(s), ${a.usage.errors} error(es), ~${a.usage.cost_estimated_usd} USD`;
  const escribe = a.writes.map((w) => `${w.root}${w.section === null ? "" : `#${w.section}`} (${w.mode})`).join(", ") || "nada";
  return `${a.name} · ${a.status} · ${a.provider}/${a.model} · lee: ${a.reads.join(", ") || "nada"} · escribe: ${escribe} · ${uso}`;
}

function lineasEjecucion(r: RunResult): string[] {
  const items = r.items.map((i) => `${i.kind === "nueva_tarea" ? "Nueva tarea" : "Prioridad"}: ${i.title ?? i.task_id ?? "?"} (${i.priority}) · ${i.reason}`);
  return [`${r.dry_run ? "Prueba" : "Ejecución"} de ${r.agent_id} · semana ${r.week} · origen ${r.source}${r.path === null ? "" : ` · ${r.path}`}`, ...items, ...r.alerts.map((a) => `Alerta: ${a}`)];
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
  const especialistas = h("div", {});
  const nombre = entrada("nombre-del-archivo (letras, números, . _ -)");
  const contenido = texto(6, "Pega aquí el YAML del agente");
  const archivo = h("input", { type: "file", attrs: { accept: ".yaml,.yml,.md,.txt" } });
  const semana = entrada("AAAA-Wnn (vacío = la actual)");
  const ia = casilla("Ejecutar con IA");

  const win = new Ventana("agentes", "Agentes y especialistas", { rutas: ["agentes", "uso"], onOpen: () => void cargar() }) as Ventana & { enfocar(id: string): void };

  archivo.addEventListener("change", () => {
    const f = archivo.files?.[0];
    if (f === undefined) return;
    void f.text().then((t) => {
      contenido.value = t;
      if (nombre.value.trim() === "") nombre.value = f.name.replace(/\.[^.]+$/, "");
    });
  });

  function tarjeta(s: ImportedAgent): HTMLElement {
    const salida = h("div", { class: "lo-result" });
    const exportado = texto(6);
    exportado.readOnly = true;
    exportado.hidden = true;
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
      h("h4", { text: `${s.name} · ${s.state} · v${s.current}` }),
      h("p", { class: "lo-muted", text: `${s.purpose}${s.area === null ? "" : ` · área ${s.area}`}` }),
      s.effective === null ? null : h("p", { class: "lo-muted", text: `Permisos efectivos: lee ${s.effective.reads.join(", ") || "nada"}; escribe ${s.effective.writes.map((w) => w.root).join(", ") || "nada"}` }),
      s.problems.length === 0 ? null : lista(s.problems.map((p) => `Problema: ${p}`)),
      h(
        "div",
        { class: "lo-actions" },
        win.boton("Probar", "probar", async () => {
          pintar(salida, lista(lineasEjecucion(await acciones.probar(s.id, ia.input.checked))));
          win.aviso(`${s.name}: prueba registrada (no escribió propuestas).`, "ok");
          await cargar();
        }),
        win.boton("Ejecutar", "ejecutar", async () => {
          pintar(salida, lista(lineasEjecucion(await acciones.ejecutar(s.id, semana.value, ia.input.checked))));
          win.aviso(`${s.name}: propuestas escritas. Revísalas en Propuestas.`, "ok");
          refrescar();
        }, "primary"),
        ...ciclo,
        win.boton("Exportar", "exportar", async () => {
          const r = await acciones.exportar(s.id);
          exportado.value = r.contenido;
          exportado.hidden = false;
          descargar(`${s.id}-v${r.version}`, r.contenido);
          win.aviso(`${s.name}: versión ${r.version} exportada.`, "ok");
        }),
      ),
      salida,
      exportado,
    );
  }

  function mostrarAgentes(a: Agents): void {
    pintar(nucleo, lista(a.agents.map(lineaAgente)), h("p", { class: "lo-muted", text: `Dueño del plan: ${a.plan_owner} · nadie escribe en: ${a.forbidden_write.join(", ") || "—"}` }));
    const imp = a.imported ?? [];
    pintar(especialistas, ...(imp.length === 0 ? [h("p", { class: "lo-empty", text: "Aún no hay especialistas. Importa uno abajo." })] : imp.map(tarjeta)));
  }

  async function cargar(): Promise<void> {
    await win.ejecutar(null, async () => {
      const [a, u] = await Promise.all([acciones.agentes(), acciones.uso()]);
      mostrarAgentes(a);
      const filas = u.rows.map((r) => `${r.agent} · ${r.ritual}: ${r.calls} llamada(s), ${r.errors} error(es), ~${r.cost_estimated_usd} USD`);
      pintar(uso, lista(filas, "Sin llamadas a modelos este mes."), h("p", { class: "lo-muted", text: `Total ${u.month}: ${u.total.calls} llamada(s), ~${u.total.cost_estimated_usd} USD` }));
    });
  }

  win.enfocar = (id: string) => {
    win.open();
    const el = especialistas.querySelector(`[data-agente="${CSS.escape(id)}"]`);
    el?.scrollIntoView({ block: "nearest" });
  };

  win.body.append(
    seccion("Agentes del núcleo", nucleo),
    seccion("Especialistas", h("div", { class: "lo-row" }, campo("Semana para ejecutar", semana), ia.el), especialistas),
    seccion(
      "Importar un especialista",
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
          await cargar();
        }, "primary"),
      ),
    ),
    seccion("Uso de modelos del mes", uso),
  );
  return win;
}
