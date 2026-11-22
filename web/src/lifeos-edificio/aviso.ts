// Aviso de conexión del edificio: el `ui-banner` de Habblaud con lo que de verdad pasa en LifeOS. El de Habblaud
// hablaba de «el servidor de Habblaud» y de `npm run dev`, que aquí no existen.
import type { UiComponent, UiContext } from "../habblaud/ui/context";
import { h, setHidden, setText } from "../habblaud/ui/dom";
import type { LifeosStore } from "./store";

const UNAUTHORIZED = 401;

export function textoAviso(error: { status: number; message: string }, hayDatos: boolean): string {
  if (error.status === UNAUTHORIZED) {
    return "No hay sesión: abre la URL que muestra lifeos-web al arrancar (pegada en la barra de direcciones) y vuelve a esta página.";
  }
  if (error.status === 0) return "El servidor de LifeOS no responde. Comprueba que lifeos-web esté en marcha.";
  const base = `No se pudieron leer los datos (${error.message}).`;
  return hayDatos ? `${base} El edificio muestra el último estado conocido.` : base;
}

export class AvisoConexion implements UiComponent {
  readonly el: HTMLElement;
  private readonly text: HTMLElement;

  constructor(
    ctx: UiContext,
    private readonly store: LifeosStore,
  ) {
    this.text = h("span", { class: "ui-banner__text" });
    const retry = h("button", { class: "ui-btn ui-btn--sm", type: "button", text: "Reintentar ahora" });
    retry.addEventListener("click", () => {
      store.reconnectNow();
      ctx.invalidate();
    });
    this.el = h("div", { class: "ui-banner", role: "alert", hidden: true }, h("span", { class: "ui-banner__dot", attrs: { "aria-hidden": "true" } }), this.text, retry);
  }

  render(): void {
    const { connection, lastError, lastStatus, snapshot } = this.store;
    const down = connection === "closed" && lastError !== null;
    setHidden(this.el, !down);
    if (down) setText(this.text, textoAviso({ status: lastStatus ?? 0, message: lastError }, snapshot !== null));
  }
}
