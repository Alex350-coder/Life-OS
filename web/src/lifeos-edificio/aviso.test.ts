// Aviso de conexión del edificio: dice qué pasa en LifeOS (sin sesión, sin servidor), no los textos de Habblaud.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { UiContext } from "../habblaud/ui/context";
import { AvisoConexion, textoAviso } from "./aviso";
import type { LifeosStore } from "./store";

describe("textoAviso", () => {
  it("sin sesión explica cómo entrar", () => {
    expect(textoAviso({ status: 401, message: "sin sesión" }, false)).toBe(
      "No hay sesión: abre la URL que muestra lifeos-web al arrancar (pegada en la barra de direcciones) y vuelve a esta página.",
    );
  });

  it("sin servidor lo dice con el comando de LifeOS", () => {
    expect(textoAviso({ status: 0, message: "Failed to fetch" }, false)).toBe("El servidor de LifeOS no responde. Comprueba que lifeos-web esté en marcha.");
  });

  it("con datos anteriores avisa de que se ve el último estado", () => {
    expect(textoAviso({ status: 500, message: "fallo interno" }, true)).toBe("No se pudieron leer los datos (fallo interno). El edificio muestra el último estado conocido.");
  });
});

describe("AvisoConexion", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("aparece con el error real y ofrece reintentar; se oculta al conectar", () => {
    const store = { connection: "closed", lastError: "sin sesión", lastStatus: 401, snapshot: null, reconnectNow: vi.fn() };
    const aviso = new AvisoConexion({ invalidate: vi.fn() } as unknown as UiContext, store as unknown as LifeosStore);
    aviso.render();
    expect(aviso.el.hidden).toBe(false);
    expect(aviso.el.textContent).toContain("No hay sesión");
    expect(aviso.el.textContent).not.toContain("Habblaud");
    aviso.el.querySelector("button")?.click();
    expect(store.reconnectNow).toHaveBeenCalled();
    store.connection = "open";
    aviso.render();
    expect(aviso.el.hidden).toBe(true);
  });
});
