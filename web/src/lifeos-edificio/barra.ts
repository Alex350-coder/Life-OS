// Barra superior del edificio (F11-T06): la de Habblaud (`ui-topbar`) con la marca LifeOS, el estado de la
// conexión con el error real, la cámara, los paneles y un botón por ventana de LifeOS. Sin contadores de
// Claude Code ni uso de cuentas: no existen en LifeOS.
import type { UiComponent, UiContext } from "../habblaud/ui/context";
import { h, iconButton, setText, setVariant } from "../habblaud/ui/dom";
import { FALLBACK_MARK, ICONS } from "../habblaud/ui/icons";
import { setSvg } from "../habblaud/ui/svg";
import type { LifeosStore } from "./store";

export interface BotonBarra {
  id: string;
  label: string;
  icon: string;
  onClick: (btn: HTMLButtonElement) => void;
  /** Ruta de rutas.json que el botón llama directamente (los que abren una ventana no la llevan). */
  ruta?: string;
}

export class Barra implements UiComponent {
  readonly el: HTMLElement;
  private readonly pill: HTMLElement;
  private readonly pillText: HTMLElement;

  constructor(
    ctx: UiContext,
    private readonly store: LifeosStore,
    botones: readonly BotonBarra[],
  ) {
    const mark = h("span", { class: "ui-brand__mark" });
    setSvg(mark, FALLBACK_MARK);
    this.pillText = h("span", { class: "ui-pill__text" });
    this.pill = h("span", { class: "ui-pill", role: "status", attrs: { "aria-live": "polite" } }, h("span", { class: "ui-pill__dot" }), this.pillText);
    const brand = h(
      "div",
      { class: "ui-brand" },
      mark,
      h("div", { class: "ui-brand__text" }, h("span", { class: "ui-brand__name lo-brand", text: "LifeOS" }), h("div", { class: "ui-topbar__status" }, this.pill)),
    );
    const acciones = h(
      "nav",
      { class: "lo-nav", attrs: { "aria-label": "Ventanas de LifeOS" } },
      ...botones.map((b) => {
        const btn = h("button", { class: "ui-btn ui-btn--sm lo-nav__btn", type: "button", title: b.label, attrs: { "data-boton": b.id, ...(b.ruta === undefined ? {} : { "data-ruta": b.ruta }) } });
        const icon = h("span", { class: "lo-nav__icon", attrs: { "aria-hidden": "true" } });
        setSvg(icon, b.icon);
        btn.append(icon, h("span", { class: "lo-nav__label", text: b.label }));
        btn.addEventListener("click", () => b.onClick(btn));
        return btn;
      }),
    );
    const camara = h(
      "div",
      { class: "ui-btn-group", role: "group", attrs: { "aria-label": "Cámara" } },
      iconButton(ICONS.overview, "Vista general (O)", () => ctx.camera("overview")),
      iconButton(ICONS.zoomOut, "Alejar", () => ctx.camera("zoomOut")),
      iconButton(ICONS.zoomIn, "Acercar", () => ctx.camera("zoomIn")),
    );
    const paneles = h(
      "div",
      { class: "ui-btn-group", role: "group", attrs: { "aria-label": "Paneles" } },
      iconButton(ICONS.feed, "Actividad ( ] )", () => ctx.togglePanel("feed")),
      iconButton(ICONS.help, "Ayuda (?)", () => ctx.openHelp()),
    );
    this.el = h(
      "header",
      { class: "ui-topbar lo-topbar", role: "banner" },
      h("div", { class: "ui-topbar__left" }, iconButton(ICONS.sidebar, "Panel lateral ( [ )", () => ctx.togglePanel("sidebar"), "ui-btn-sidebar"), brand),
      acciones,
      h("div", { class: "ui-topbar__right" }, camara, paneles),
    );
  }

  render(): void {
    const conn = this.store.connection;
    const label = conn === "open" ? "Conectado" : conn === "closed" ? "Sin conexión" : "Conectando…";
    setText(this.pillText, label);
    setVariant(this.pill, "is-", conn);
    this.pill.title = conn === "closed" && this.store.lastError !== null ? this.store.lastError : "Datos de tu bóveda a través de la API local";
  }
}
