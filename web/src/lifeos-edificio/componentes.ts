// Piezas de presentación de las ventanas del edificio: pestañas, listas paginadas, filas con etiquetas, datos y
// desplegables. Cada ventana enseña poco a la vez. Todo el texto entra con textContent (nunca HTML).
import { h, iconButton, setText } from "../habblaud/ui/dom";
import { ICONS } from "../habblaud/ui/icons";

export type Tono = "neutro" | "ok" | "info" | "alerta" | "error";

export interface Etiqueta {
  texto: string;
  tono?: Tono;
}

export interface Fila {
  titulo: string;
  detalle?: string;
  etiquetas?: readonly Etiqueta[];
}

export interface EstadoPagina {
  pagina: number;
}

export interface OpcionesPagina<T> {
  porPagina?: number;
  vacio?: string;
  contenedor?: "ul" | "div";
  /** Recuerda la página entre un repintado y el siguiente (p. ej. tras aceptar una propuesta). */
  estado?: EstadoPagina;
  /** Con un elemento por página: nombre que muestra el paginador («Carrera · 1 de 4»). */
  nombre?: (item: T) => string;
}

const POR_PAGINA = 5;

export function etiqueta({ texto, tono = "neutro" }: Etiqueta): HTMLElement {
  return h("span", { class: `lo-tag lo-tag--${tono}`, text: texto });
}

/** Título con sus etiquetas a la derecha y, debajo, el detalle en pequeño. */
export function fila({ titulo, detalle, etiquetas = [] }: Fila): HTMLLIElement {
  return h(
    "li",
    { class: "lo-item" },
    h("div", { class: "lo-item__head" }, h("span", { class: "lo-item__title", text: titulo }), ...etiquetas.map(etiqueta)),
    detalle === undefined || detalle === "" ? null : h("p", { class: "lo-item__meta", text: detalle }),
  );
}

/** Lista que muestra una página cada vez. Los elementos se crean una sola vez: lo escrito en ellos se conserva. */
export function paginar<T>(items: readonly T[], crear: (item: T) => HTMLElement, opts: OpcionesPagina<T> = {}): HTMLElement {
  const { porPagina = POR_PAGINA, vacio = "Nada que mostrar.", contenedor = "ul", estado = { pagina: 0 }, nombre } = opts;
  if (items.length === 0) return h("p", { class: "lo-empty", text: vacio });
  const total = items.length;
  const ultima = Math.ceil(total / porPagina) - 1;
  const creados = new Map<number, HTMLElement>();
  const lista = h(contenedor, { class: contenedor === "ul" ? "lo-list" : "lo-stack" });
  const info = h("span", { class: "lo-pager__info", attrs: { "aria-live": "polite" } });
  const anterior = iconButton(ICONS.chevronLeft, "Página anterior", () => ir(estado.pagina - 1), "ui-icon-btn--sm lo-pager__btn");
  const siguiente = iconButton(ICONS.chevronRight, "Página siguiente", () => ir(estado.pagina + 1), "ui-icon-btn--sm lo-pager__btn");

  function elemento(i: number, item: T): HTMLElement {
    const el = creados.get(i) ?? crear(item);
    creados.set(i, el);
    return el;
  }

  function rango(desde: number, hasta: number): string {
    const unico = items[desde];
    if (nombre !== undefined && porPagina === 1 && unico !== undefined) return `${nombre(unico)} · ${desde + 1} de ${total}`;
    return desde + 1 === hasta ? `${hasta} de ${total}` : `${desde + 1}–${hasta} de ${total}`;
  }

  function ir(pagina: number): void {
    estado.pagina = Math.max(0, Math.min(ultima, pagina));
    const desde = estado.pagina * porPagina;
    const hasta = Math.min(total, desde + porPagina);
    lista.replaceChildren(...items.slice(desde, hasta).map((item, i) => elemento(desde + i, item)));
    setText(info, rango(desde, hasta));
    anterior.disabled = estado.pagina === 0;
    siguiente.disabled = estado.pagina === ultima;
  }

  ir(estado.pagina);
  const paginador = ultima === 0 ? null : h("nav", { class: "lo-pager", attrs: { "aria-label": "Páginas" } }, anterior, info, siguiente);
  return h("div", { class: "lo-pages" }, lista, paginador);
}

/** Lista paginada de líneas sueltas de texto. */
export function lineas(items: readonly string[], vacio = "Nada que mostrar."): HTMLElement {
  return paginar(items, (t) => fila({ titulo: t }), { vacio });
}

export interface DefPestana {
  id: string;
  label: string;
  panel: readonly Node[];
}

export interface Pestanas {
  el: HTMLElement;
  mostrar(id: string): void;
  /** Número junto al nombre de la pestaña (0 lo quita). */
  contador(id: string, n: number): void;
}

/** Pestañas accesibles (flechas, Inicio y Fin) con el aspecto del `ui-seg` de Habblaud. */
export function pestanas(prefijo: string, defs: readonly DefPestana[]): Pestanas {
  const partes = defs.map((d, i) => {
    const num = h("span", { class: "lo-tab__count", hidden: true });
    const tab = h(
      "button",
      { class: "ui-seg__opt lo-tab", type: "button", role: "tab", tabIndex: i === 0 ? 0 : -1, attrs: { id: `lo-tab-${prefijo}-${d.id}`, "aria-controls": `lo-panel-${prefijo}-${d.id}`, "data-pestana": d.id } },
      h("span", { class: "lo-tab__label", text: d.label }),
      num,
    );
    const panel = h("div", { class: "lo-panel", role: "tabpanel", attrs: { id: `lo-panel-${prefijo}-${d.id}`, "aria-labelledby": `lo-tab-${prefijo}-${d.id}` } }, ...d.panel);
    return { id: d.id, tab, panel, num };
  });

  function mostrar(id: string): void {
    for (const p of partes) {
      const activa = p.id === id;
      p.tab.setAttribute("aria-selected", String(activa));
      p.tab.tabIndex = activa ? 0 : -1;
      p.panel.hidden = !activa;
    }
  }

  function tecla(e: KeyboardEvent, i: number): void {
    const n = partes.length;
    const destino = { ArrowRight: (i + 1) % n, ArrowLeft: (i + n - 1) % n, Home: 0, End: n - 1 }[e.key];
    const p = destino === undefined ? undefined : partes[destino];
    if (p === undefined) return;
    e.preventDefault();
    mostrar(p.id);
    p.tab.focus();
  }

  partes.forEach((p, i) => {
    p.tab.addEventListener("click", () => mostrar(p.id));
    p.tab.addEventListener("keydown", (e) => tecla(e, i));
  });
  const primera = partes[0];
  if (primera !== undefined) mostrar(primera.id);
  return {
    el: h("div", { class: "lo-tabs" }, h("div", { class: "ui-seg lo-tablist", role: "tablist" }, ...partes.map((p) => p.tab)), ...partes.map((p) => p.panel)),
    mostrar,
    contador(id, n) {
      const p = partes.find((x) => x.id === id);
      if (p === undefined) return;
      setText(p.num, n > 0 ? String(n) : "");
      p.num.hidden = n <= 0;
    },
  };
}

/** Detalle que se abre solo si se pide. */
export function desplegable(titulo: string, ...children: (Node | null)[]): HTMLDetailsElement {
  return h("details", { class: "lo-more" }, h("summary", { class: "lo-more__title", text: titulo }), h("div", { class: "lo-more__body" }, ...children));
}

/** Pares nombre → valor. */
export function datos(pares: readonly (readonly [string, string])[]): HTMLElement {
  return h("dl", { class: "lo-data" }, ...pares.flatMap(([k, v]) => [h("dt", { class: "lo-data__key", text: k }), h("dd", { class: "lo-data__value", text: v })]));
}

/** Cifra destacada con su nombre y una nota. */
export function cifra(nombre: string, valor: string, nota = ""): HTMLElement {
  return h("div", { class: "lo-stat" }, h("span", { class: "lo-stat__name", text: nombre }), h("strong", { class: "lo-stat__value", text: valor }), nota === "" ? null : h("span", { class: "lo-stat__note", text: nota }));
}
