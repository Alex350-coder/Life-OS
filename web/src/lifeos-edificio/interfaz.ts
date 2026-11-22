// Montaje de la interfaz del edificio (F11-T06): el createUI de Habblaud reducido a lo que LifeOS usa (barra,
// panel lateral, actividad, avisos, aviso de conexión y ficha al pasar el ratón) más las ventanas de LifeOS.
// Clic en un agente o en una sala abre su oficina. No hay terminal, World, timelapse ni ajustes de Habblaud:
// sus métodos del contexto no tienen botón que los llame.
import type { PanelName, UiComponent, UiContext } from "../habblaud/ui/context";
import { h } from "../habblaud/ui/dom";
import { FeedPanel } from "../habblaud/ui/feed";
import { HoverTip } from "../habblaud/ui/hovertip";
import { loadPrefs, safeLocalStorage, savePrefs, worldOptionsFrom, type UiPrefs } from "../habblaud/ui/prefs";
import { Sidebar } from "../habblaud/ui/sidebar";
import { Toasts } from "../habblaud/ui/toasts";
import { FreeArea } from "../habblaud/ui/viewport";
import type { Selection, WorldApi } from "../habblaud/world/api";
import { acciones, RUTA } from "./acciones";
import { AvisoConexion } from "./aviso";
import { COACH_ROOM, JEFE_ROOM, OWNER_ID, OWNER_ROOM } from "./adapter";
import { Barra, type BotonBarra } from "./barra";
import { ICONOS } from "./iconos";
import type { LifeosStore } from "./store";
import type { Ventana } from "./ventana";
import { crearAgentes } from "./ventanas/agentes";
import { crearCoach } from "./ventanas/coach";
import { crearAyuda, crearCalendario, crearHistorial, crearIndicadores, crearPropuestas } from "./ventanas/consultas";
import { crearJefe } from "./ventanas/jefe";
import { crearTareas } from "./ventanas/tareas";

const NARROW = "(max-width: 900px)";
const CLOCK_MS = 5_000;

export interface Ventanas {
  jefe: Ventana;
  coach: Ventana;
  tareas: Ventana;
  agentes: ReturnType<typeof crearAgentes>;
  propuestas: Ventana;
  historial: Ventana;
  indicadores: Ventana;
  calendario: Ventana;
  ayuda: Ventana;
}

export function crearVentanas(refrescar: () => void): Ventanas {
  return {
    jefe: crearJefe(refrescar),
    coach: crearCoach(refrescar),
    tareas: crearTareas(refrescar),
    agentes: crearAgentes(refrescar),
    propuestas: crearPropuestas(refrescar),
    historial: crearHistorial(),
    indicadores: crearIndicadores(),
    calendario: crearCalendario(),
    ayuda: crearAyuda(),
  };
}

/** La oficina de cada agente o sala: el propietario, el Jefe, el Coach o un especialista. */
export function oficina(v: Ventanas, sel: Selection, roomOf: (agentId: string) => string | undefined): (() => void) | null {
  if (sel === null) return null;
  const room = sel.type === "room" ? sel.id : roomOf(sel.id);
  if (room === OWNER_ROOM || (sel.type === "agent" && sel.id === OWNER_ID)) return () => v.tareas.open();
  if (room === JEFE_ROOM) return () => v.jefe.open();
  if (room === COACH_ROOM) return () => v.coach.open();
  if (room?.startsWith("especialista:") === true) return () => v.agentes.enfocar(room.slice("especialista:".length));
  return null;
}

export function botonesBarra(v: Ventanas, salir: () => Promise<void>): BotonBarra[] {
  const abrir = (w: Ventana) => () => w.toggle();
  return [
    { id: "tareas", label: "Tareas", icon: ICONOS.tareas, onClick: abrir(v.tareas) },
    { id: "jefe", label: "Semana", icon: ICONOS.jefe, onClick: abrir(v.jefe) },
    { id: "coach", label: "Hoy", icon: ICONOS.coach, onClick: abrir(v.coach) },
    { id: "agentes", label: "Agentes", icon: ICONOS.agentes, onClick: abrir(v.agentes) },
    { id: "propuestas", label: "Propuestas", icon: ICONOS.propuestas, onClick: abrir(v.propuestas) },
    { id: "historial", label: "Historial", icon: ICONOS.historial, onClick: abrir(v.historial) },
    { id: "indicadores", label: "Indicadores", icon: ICONOS.indicadores, onClick: abrir(v.indicadores) },
    { id: "calendario", label: "Calendario", icon: ICONOS.calendario, onClick: abrir(v.calendario) },
    { id: "clasica", label: "Vista clásica", icon: ICONOS.clasica, onClick: () => location.assign("/") },
    { id: "salir", label: "Cerrar sesión", icon: ICONOS.salir, ruta: RUTA.cerrarSesion, onClick: () => void salir() },
  ];
}

export function montarInterfaz(root: HTMLElement, store: LifeosStore, world: WorldApi): Ventanas {
  const storage = safeLocalStorage();
  let prefs: UiPrefs = loadPrefs(storage);
  let selection: Selection = world.getSelection();
  const narrowMq = matchMedia(NARROW);
  const panels: Record<PanelName, boolean> = { sidebar: !narrowMq.matches && prefs.sidebarOpen, feed: !narrowMq.matches && prefs.feedOpen };
  const live = h("div", { class: "ui-sr", role: "status", attrs: { "aria-live": "polite" } });
  let rafId = 0;
  const refrescar = () => void store.refresh();
  const ventanas = crearVentanas(refrescar);

  const ctx: UiContext = {
    store,
    world,
    root,
    get prefs() {
      return prefs;
    },
    updatePrefs(patch) {
      prefs = { ...prefs, ...patch };
      savePrefs(storage, prefs);
      world.setOptions(worldOptionsFrom(prefs));
      invalidate();
    },
    now: () => Date.now(),
    account: () => undefined,
    agent: (id) => store.agent(id),
    selection: () => selection,
    select(sel, opts) {
      if (sel && opts?.focus) area.setIntent(sel);
      else area.clearIntent();
      selection = sel;
      area.selectingFromUi();
      world.select(sel, opts);
      applyLayout({ refocus: false });
    },
    focusSelection() {
      if (selection) ctx.select(selection, { focus: true });
    },
    camera(action) {
      area.clearIntent();
      if (action === "overview") world.overview();
      else world.zoomBy(action === "zoomIn" ? 1.25 : 1 / 1.25);
    },
    invalidate: () => invalidate(),
    togglePanel(name, open) {
      const next = open ?? !panels[name];
      if (panels[name] === next) return;
      panels[name] = next;
      if (!narrowMq.matches) ctx.updatePrefs(name === "sidebar" ? { sidebarOpen: next } : { feedOpen: next });
      applyLayout();
      invalidate();
    },
    isPanelOpen: (name) => panels[name],
    isNarrow: () => narrowMq.matches,
    announce(text) {
      live.textContent = "";
      requestAnimationFrame(() => (live.textContent = text));
    },
    focusSearch() {
      ctx.togglePanel("sidebar", true);
      requestAnimationFrame(() => sidebar.focusSearch());
    },
    openHelp: () => ventanas.ayuda.open(),
    // Sin equivalente en LifeOS y sin botón que las llame (ADR-0022): ajustes, World, «Acerca de» y timelapse.
    toggleSettings: () => undefined,
    toggleWorld: () => undefined,
    openAbout: () => undefined,
    toggleTimelapse: () => undefined,
    isTimelapseOpen: () => false,
  };

  async function salir(): Promise<void> {
    await acciones.cerrarSesion();
    location.assign("/");
  }

  const barra = new Barra(ctx, store, botonesBarra(ventanas, salir));
  const sidebar = new Sidebar(ctx);
  const feed = new FeedPanel(ctx);
  const toasts = new Toasts(ctx);
  const banner = new AvisoConexion(ctx, store);
  const tip = new HoverTip(ctx);
  const capaVentanas = h("div", { class: "lo-windows" }, ...Object.values(ventanas).map((w: Ventana) => w.el));
  const drawer = h("div", { hidden: true });

  root.classList.add("ui-root");
  root.append(barra.el, sidebar.el, feed.el, toasts.el, banner.el, tip.el, capaVentanas, drawer, live);
  const area = new FreeArea(world, { root, topbar: barra.el, sidebar: sidebar.el, drawer, feed: feed.el }, () => ({
    sidebar: panels.sidebar,
    feed: panels.feed,
    drawer: false,
    narrow: narrowMq.matches,
  }));
  const components: UiComponent[] = [barra, sidebar, feed, toasts, banner, tip];

  function renderAll(): void {
    rafId = 0;
    for (const c of components) {
      try {
        c.render();
      } catch (err) {
        console.error("[edificio] fallo al pintar", c.constructor.name, err); // eslint-disable-line no-console -- mismo registro que el createUI de Habblaud
      }
    }
  }

  function invalidate(): void {
    if (!rafId) rafId = requestAnimationFrame(renderAll);
  }

  function applyLayout(opts: { refocus?: boolean } = {}): void {
    root.classList.toggle("has-sidebar", panels.sidebar);
    root.classList.toggle("has-feed", panels.feed);
    root.classList.toggle("is-narrow", narrowMq.matches);
    area.sync(opts);
  }

  store.on("snapshot", () => invalidate());
  store.on("connection", () => invalidate());
  world.onSelect((sel) => {
    const prev = selection;
    selection = sel;
    area.selectionChangedExternally(prev, sel);
    applyLayout();
    invalidate();
    oficina(ventanas, sel, (id) => store.agent(id)?.roomId)?.();
  });
  narrowMq.addEventListener("change", () => {
    panels.sidebar = !narrowMq.matches && prefs.sidebarOpen;
    panels.feed = !narrowMq.matches && prefs.feedOpen;
    applyLayout();
    invalidate();
  });
  setInterval(() => {
    if (!document.hidden) invalidate();
  }, CLOCK_MS);
  addEventListener("keydown", (e) => onKey(e, ctx, () => selection));

  world.setOptions(worldOptionsFrom(prefs));
  applyLayout();
  renderAll();
  return ventanas;
}

function onKey(e: KeyboardEvent, ctx: UiContext, selection: () => Selection): void {
  const t = e.target;
  const typing = t instanceof HTMLInputElement || t instanceof HTMLTextAreaElement || t instanceof HTMLSelectElement;
  if (e.defaultPrevented || e.ctrlKey || e.metaKey || e.altKey || typing) return;
  if (e.key === "Escape" && selection() !== null) ctx.select(null);
  else if (e.key === "o" || e.key === "O") ctx.camera("overview");
  else if (e.key === "[") ctx.togglePanel("sidebar");
  else if (e.key === "]") ctx.togglePanel("feed");
  else if (e.key === "?") ctx.openHelp();
  else if (e.key === "/") {
    e.preventDefault();
    ctx.focusSearch();
  }
}
