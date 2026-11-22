// Entrada de edificio.html (F11-T03, ADR-0022). Como el main.ts de Habblaud: primero el diccionario español
// (varios módulos llaman a tr() al cargarse), después el mundo y la interfaz. Sin sesión, la página carga
// pero no ve datos: la barra lo muestra y la vista clásica sigue en «/».
import "@fontsource/pixelify-sans/400.css";
import "@fontsource/pixelify-sans/500.css";
import "@fontsource/pixelify-sans/600.css";
import "@fontsource/pixelify-sans/700.css";
import "../habblaud/ui/styles.css";
import "./edificio.css";
import { registerDictionary, setLocale } from "../habblaud/shared/i18n";
import { ES } from "../habblaud/shared/locales/es";

registerDictionary("es", ES);
setLocale("es");

async function start(): Promise<void> {
  const [{ createWorld }, { LifeosStore }, { montarInterfaz }] = await Promise.all([
    import("../habblaud/world/index"),
    import("./store"),
    import("./interfaz"),
  ]);
  const canvas = document.getElementById("world");
  const root = document.getElementById("ui");
  if (!(canvas instanceof HTMLCanvasElement) || root === null) return;
  const store = new LifeosStore();
  const world = createWorld(canvas, store);
  montarInterfaz(root, store, world);
  store.start();
}

void start();
