// Piezas de las ventanas del edificio: pestañas, paginación y filas. Evitan que una ventana lo muestre todo a la vez.
import { describe, expect, it } from "vitest";
import { desplegable, fila, paginar, pestanas } from "./componentes";

const N = Array.from({ length: 12 }, (_, i) => `elemento ${i + 1}`);
const textos = (el: Element) => [...el.querySelectorAll("li")].map((li) => li.textContent);

function boton(el: Element, nombre: string): HTMLButtonElement {
  const b = el.querySelector<HTMLButtonElement>(`button[aria-label="${nombre}"]`);
  if (b === null) throw new Error(`no hay botón «${nombre}»`);
  return b;
}

describe("paginar", () => {
  it("muestra solo una página y dice cuál es", () => {
    const el = paginar(N, (t) => fila({ titulo: t }), { porPagina: 5 });
    expect(textos(el)).toEqual(N.slice(0, 5));
    expect(el.querySelector(".lo-pager__info")?.textContent).toBe("1–5 de 12");
    expect(boton(el, "Página anterior").disabled).toBe(true);
  });

  it("avanza, retrocede y se detiene en los extremos", () => {
    const el = paginar(N, (t) => fila({ titulo: t }), { porPagina: 5 });
    boton(el, "Página siguiente").click();
    boton(el, "Página siguiente").click();
    expect(textos(el)).toEqual(N.slice(10));
    expect(el.querySelector(".lo-pager__info")?.textContent).toBe("11–12 de 12");
    expect(boton(el, "Página siguiente").disabled).toBe(true);
    boton(el, "Página anterior").click();
    expect(textos(el)).toEqual(N.slice(5, 10));
  });

  it("si cabe en una página no hay paginador, y vacío lo dice", () => {
    expect(paginar(N.slice(0, 5), (t) => fila({ titulo: t }), { porPagina: 5 }).querySelector(".lo-pager")).toBeNull();
    expect(paginar([], () => fila({ titulo: "" }), { vacio: "Nada." }).textContent).toBe("Nada.");
  });

  it("recuerda la página al repintar y no se pasa si ahora hay menos elementos", () => {
    const estado = { pagina: 0 };
    const pinta = (items: string[]) => paginar(items, (t) => fila({ titulo: t }), { porPagina: 5, estado });
    boton(pinta(N), "Página siguiente").click();
    boton(pinta(N), "Página siguiente").click();
    expect(estado.pagina).toBe(2);
    expect(textos(pinta(N.slice(0, 6)))).toEqual(["elemento 6"]);
  });

  it("con un elemento por página, el paginador puede nombrarlo", () => {
    const el = paginar(["Carrera", "Hobbies"], (t) => fila({ titulo: t }), { porPagina: 1, nombre: (t) => t });
    expect(el.querySelector(".lo-pager__info")?.textContent).toBe("Carrera · 1 de 2");
  });

  it("lo escrito en un elemento se conserva al cambiar de página", () => {
    const campos = [0, 1].map(() => document.createElement("input"));
    const el = paginar(campos, (c) => c, { porPagina: 1, contenedor: "div" });
    const primero = el.querySelector("input");
    if (primero === null) throw new Error("sin campo");
    primero.value = "50";
    boton(el, "Página siguiente").click();
    boton(el, "Página anterior").click();
    expect(el.querySelector("input")?.value).toBe("50");
  });
});

describe("pestanas", () => {
  const crear = () =>
    pestanas("demo", [
      { id: "a", label: "Uno", panel: [document.createTextNode("contenido A")] },
      { id: "b", label: "Dos", panel: [document.createTextNode("contenido B")] },
    ]);
  const tabsDe = (el: Element) => [...el.querySelectorAll<HTMLElement>('[role="tab"]')];

  it("muestra un panel cada vez, con los roles de accesibilidad", () => {
    const p = crear();
    const tabs = tabsDe(p.el);
    const paneles = [...p.el.querySelectorAll<HTMLElement>('[role="tabpanel"]')];
    expect(tabs.map((t) => t.getAttribute("aria-selected"))).toEqual(["true", "false"]);
    expect(paneles.map((x) => x.hidden)).toEqual([false, true]);
    tabs[1]?.click();
    expect(paneles.map((x) => x.hidden)).toEqual([true, false]);
    expect(paneles[1]?.getAttribute("aria-labelledby")).toBe(tabs[1]?.id);
  });

  it("se recorre con las flechas y solo la pestaña activa entra en el tabulador", () => {
    const tabs = tabsDe(crear().el);
    tabs[0]?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(tabs.map((t) => t.tabIndex)).toEqual([-1, 0]);
    tabs[1]?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    expect(tabs.map((t) => t.tabIndex)).toEqual([0, -1]);
    tabs[0]?.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    expect(tabs.map((t) => t.tabIndex)).toEqual([-1, 0]);
  });

  it("puede mostrar una pestaña y su contador desde el código", () => {
    const p = crear();
    p.mostrar("b");
    p.contador("b", 7);
    expect(p.el.querySelector('[aria-selected="true"]')?.textContent).toBe("Dos7");
    p.contador("b", 0);
    expect(p.el.querySelector('[aria-selected="true"]')?.textContent).toBe("Dos");
  });
});

describe("fila y desplegable", () => {
  it("la fila separa título, etiquetas y detalle, siempre como texto", () => {
    const li = fila({ titulo: "<img src=x>", detalle: "vence mañana", etiquetas: [{ texto: "propuesta", tono: "alerta" }] });
    expect(li.querySelector(".lo-item__title")?.textContent).toBe("<img src=x>");
    expect(li.querySelector("img")).toBeNull();
    expect(li.querySelector(".lo-tag--alerta")?.textContent).toBe("propuesta");
    expect(li.querySelector(".lo-item__meta")?.textContent).toBe("vence mañana");
  });

  it("el desplegable empieza cerrado", () => {
    const d = desplegable("Permisos", document.createTextNode("lee todo"));
    expect(d.open).toBe(false);
    expect(d.querySelector("summary")?.textContent).toBe("Permisos");
  });
});
