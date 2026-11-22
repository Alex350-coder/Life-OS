// Ventanas de LifeOS en el edificio (F11-T06): el diálogo de Habblaud (`ui-dialog`), no modal y movible con
// su `Movable`, para que el mundo siga a la vista. Todo el texto entra con textContent (nunca HTML): lo que
// llega de la bóveda se muestra tal cual. Cada botón que llama a la API lleva `data-ruta` (rutas.json).
import { ApiError } from "../api/client";
import { h, iconButton } from "../habblaud/ui/dom";
import { ICONS } from "../habblaud/ui/icons";
import { Movable } from "../habblaud/ui/movable";
import { RUTA, type RutaId } from "./acciones";

export type Nivel = "ok" | "info" | "error";

/** Mensaje legible de cualquier fallo; con los conflictos del validador cuando el servidor los manda. */
export function errorText(err: unknown): string {
  if (!(err instanceof ApiError)) return err instanceof Error ? err.message : "error desconocido";
  const data = err.data as { conflicts?: { message: string }[] } | null;
  const conflicts = data?.conflicts?.map((c) => c.message) ?? [];
  return conflicts.length === 0 ? err.message : `${err.message}: ${conflicts.join("; ")}`;
}

/** Sustituye el contenido de `el`; los null se ignoran (partes opcionales). */
export function pintar(el: Element, ...children: (Node | null)[]): void {
  el.replaceChildren(...children.filter((c): c is Node => c !== null));
}

export function campo(label: string, control: HTMLElement): HTMLElement {
  return h("label", { class: "lo-field" }, h("span", { class: "lo-field__label", text: label }), control);
}

export function entrada(placeholder = "", value = ""): HTMLInputElement {
  const el = h("input", { class: "ui-rename__input lo-input", type: "text", attrs: { placeholder, autocomplete: "off", spellcheck: "false" } });
  el.value = value;
  return el;
}

export function texto(rows = 3, placeholder = ""): HTMLTextAreaElement {
  return h("textarea", { class: "ui-rename__input lo-input", attrs: { rows, placeholder } });
}

export function selector(options: readonly (readonly [string, string])[], value = ""): HTMLSelectElement {
  const el = h("select", { class: "ui-rename__input lo-input" });
  for (const [v, label] of options) el.append(h("option", { text: label, attrs: { value: v } }));
  el.value = value;
  return el;
}

export function casilla(label: string): { el: HTMLElement; input: HTMLInputElement } {
  const input = h("input", { type: "checkbox" });
  return { el: h("label", { class: "lo-check" }, input, h("span", { text: label })), input };
}

/** Lista de líneas de texto (vacía: muestra `empty`). */
export function lista(items: readonly string[], empty = "Nada que mostrar."): HTMLElement {
  if (items.length === 0) return h("p", { class: "lo-empty", text: empty });
  return h("ul", { class: "lo-list" }, ...items.map((t) => h("li", { text: t })));
}

export function seccion(titulo: string, ...children: (Node | null)[]): HTMLElement {
  return h("section", { class: "lo-sec" }, h("h3", { text: titulo }), ...children);
}

/** Deshabilita un botón de forma que `ejecutar` no lo vuelva a habilitar al terminar. */
export function bloquear(btn: HTMLButtonElement, bloqueado: boolean): void {
  btn.dataset.bloqueado = String(bloqueado);
  if (btn.getAttribute("aria-busy") !== "true") btn.disabled = bloqueado;
}

export class Ventana {
  readonly el: HTMLDialogElement;
  readonly body: HTMLElement;
  private readonly status: HTMLElement;
  private readonly onOpen: (() => void) | undefined;
  private readonly declared = new Set<RutaId>();

  constructor(
    readonly id: string,
    titulo: string,
    opts: { onOpen?: () => void; rutas?: RutaId[] } = {},
  ) {
    this.onOpen = opts.onOpen;
    const titleId = `lo-win-${id}-title`;
    const head = h(
      "div",
      { class: "ui-dialog__head lo-win__head" },
      h("h2", { text: titulo, attrs: { id: titleId } }),
      iconButton(ICONS.close, "Cerrar (Esc)", () => this.close(), "ui-icon-btn--sm"),
    );
    this.status = h("p", { class: "lo-status", role: "status", attrs: { "aria-live": "polite" }, hidden: true });
    this.body = h("div", { class: "lo-win__body" });
    this.el = h("dialog", { class: "ui-dialog lo-win", attrs: { "aria-labelledby": titleId, "data-ventana": id } }, head, this.status, this.body);
    for (const r of opts.rutas ?? []) this.declare(r);
    this.el.addEventListener("keydown", (e) => {
      e.stopPropagation(); // los atajos del mundo no se disparan mientras escribes aquí
      if (e.key === "Escape") this.close();
    });
    new Movable(this.el, head, { key: `lifeos:ventana:${id}`, enabled: () => !matchMedia("(max-width: 900px)").matches });
  }

  /** Rutas que la ventana usa al abrirse (lecturas); quedan en `data-rutas` para la prueba de inventario. */
  declare(ruta: RutaId): void {
    this.declared.add(ruta);
    this.el.dataset.rutas = [...this.declared].map((r) => RUTA[r]).join("|");
  }

  get isOpen(): boolean {
    return this.el.open;
  }

  open(): void {
    if (!this.el.open) {
      if (typeof this.el.show === "function") this.el.show();
      else this.el.setAttribute("open", "");
    }
    this.el.parentElement?.append(this.el); // la última abierta queda encima
    this.onOpen?.();
  }

  close(): void {
    if (typeof this.el.close === "function") this.el.close();
    else this.el.removeAttribute("open");
  }

  toggle(): void {
    if (this.isOpen) this.close();
    else this.open();
  }

  aviso(text: string, nivel: Nivel = "info"): void {
    this.status.textContent = text;
    this.status.hidden = text === "";
    this.status.className = `lo-status lo-status--${nivel}`;
  }

  /**
   * Botón que ejecuta una acción real. Mientras corre queda ocupado; el error se muestra en la ventana.
   * Devuelve el botón para poder colocarlo donde haga falta.
   */
  boton(label: string, ruta: RutaId, run: () => Promise<void>, variant: "primary" | "danger" | "" = ""): HTMLButtonElement {
    const cls = `ui-btn ui-btn--sm${variant === "" ? "" : ` ui-btn--${variant}`}`;
    const btn = h("button", { class: cls, type: "button", text: label, attrs: { "data-ruta": RUTA[ruta] } });
    btn.addEventListener("click", () => void this.ejecutar(btn, run));
    return btn;
  }

  /** Corre una carga o acción con el estado ocupado y el error visible. */
  async ejecutar(btn: HTMLButtonElement | null, run: () => Promise<void>): Promise<void> {
    if (btn?.getAttribute("aria-busy") === "true") return;
    btn?.setAttribute("aria-busy", "true");
    if (btn) btn.disabled = true;
    try {
      await run();
    } catch (err) {
      this.aviso(errorText(err), "error");
    } finally {
      btn?.removeAttribute("aria-busy");
      if (btn) btn.disabled = btn.dataset.bloqueado === "true";
    }
  }
}
