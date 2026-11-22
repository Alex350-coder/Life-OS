// Montaje de la interfaz con un mundo falso (el canvas real se prueba en la E2E del edificio).
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Selection, WorldApi } from "../habblaud/world/api";
import { installFakeApi, ok } from "../test/fakeApi";
import { montarInterfaz } from "./interfaz";
import { LifeosStore } from "./store";

function mundoFalso() {
  let onSelect: (s: Selection) => void = () => undefined;
  const world = {
    getSelection: () => null,
    select: vi.fn(),
    onSelect: (cb: (s: Selection) => void) => {
      onSelect = cb;
      return () => undefined;
    },
    onHover: () => () => undefined,
    onSocialEvent: () => () => undefined,
    setOptions: vi.fn(),
    getOptions: () => ({}),
    overview: vi.fn(),
    zoomBy: vi.fn(),
    focusAgent: vi.fn(),
    focusRoom: vi.fn(),
    setViewInsets: vi.fn(),
    screenPositionOf: () => null,
  };
  return { world: world as unknown as WorldApi, raw: world, seleccionar: (s: Selection) => onSelect(s) };
}

beforeEach(() => {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener: () => undefined }));
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => setTimeout(() => cb(0), 0));
});
afterEach(() => vi.unstubAllGlobals());

describe("montarInterfaz", () => {
  it("monta la barra LifeOS, los paneles de Habblaud y las ventanas; un clic en el Jefe abre su oficina", async () => {
    installFakeApi({ "GET /api/semana": ok({ week: "2026-W45", zone: "America/Lima", path: null, status: null, origin: null, revision: 0, versions: 0, generated: null, justification: "", days: [], blocks: [], load: [] }) });
    const root = document.createElement("div");
    document.body.append(root);
    const store = new LifeosStore({
      load: async () => ({
        today: null,
        agents: { month: "2026-11", agents: [], plan_owner: "jefe", forbidden_write: [], restricted_read: [], usage_malformed: 0, imported: [] },
        proposals: [],
        upcoming: [],
        history: [],
      }),
    });
    const { world, raw, seleccionar } = mundoFalso();
    const ventanas = montarInterfaz(root, store, world);
    await store.refresh();
    expect(root.querySelector(".lo-brand")?.textContent).toBe("LifeOS");
    expect(root.querySelector("#ui-sidebar")).not.toBeNull();
    expect(root.querySelectorAll("dialog.lo-win")).toHaveLength(9);
    await vi.waitFor(() => expect(root.querySelector(".ui-pill__text")?.textContent).toBe("Conectado"));

    seleccionar({ type: "agent", id: "jefe" });
    expect(ventanas.jefe.isOpen).toBe(true);

    root.querySelector<HTMLButtonElement>('[aria-label="Vista general (O)"]')?.click();
    expect(raw.overview).toHaveBeenCalled();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "?" }));
    expect(ventanas.ayuda.isOpen).toBe(true);
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "]" }));
    expect(root.classList.contains("has-feed")).toBe(false);
    root.remove();
  });
});
