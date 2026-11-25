import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

// Edificio (F11, ADR-0022/0023): la interfaz de LifeOS contra el backend real (bóveda temporal inventada,
// lunes 2026-11-02 07:30). Se usa LifeOS entero desde la interfaz de Habblaud y se comprueba con la API
// que cada acción persiste. Serie: cada prueba parte del estado que deja la anterior.
test.describe.configure({ mode: "serial" });

const HERE = dirname(fileURLToPath(import.meta.url));

function vigilar(page: Page): string[] {
  const problems: string[] = [];
  page.on("dialog", (d) => {
    problems.push(`diálogo inesperado: ${d.message()}`);
    void d.dismiss();
  });
  page.on("console", (m) => {
    if (m.type() === "error") problems.push(m.text());
  });
  page.on("pageerror", (e) => problems.push(e.message));
  return problems;
}

async function abrir(page: Page, boton: string, ventana: string) {
  await page.locator(`[data-boton="${boton}"]`).click();
  const win = page.locator(`dialog[data-ventana="${ventana}"]`);
  await expect(win).toBeVisible();
  return win;
}

async function api<T>(page: Page, path: string): Promise<T> {
  const r = await page.request.get(path);
  expect(r.ok()).toBe(true);
  return ((await r.json()) as { data: T }).data;
}

test("sin plan el edificio se puebla y el Jefe espera (hallazgo 1)", async ({ page }) => {
  const problems = vigilar(page);
  await page.goto("/");
  await expect(page.locator("canvas#world")).toBeVisible();
  await expect(page.locator(".ui-pill__text")).toHaveText("Conectado");
  await expect(page.locator("#ui-sidebar")).toContainText("Oficina del Jefe");
  await expect(page.locator("#ui-sidebar")).toContainText("Oficina del Coach");
  await expect(page.locator("#ui-sidebar")).toContainText("Tu espacio");
  // Sin plan, GET /api/hoy responde 409 a propósito; el navegador anota cualquier 4xx en la consola.
  expect(problems.filter((p) => !p.includes("status of 409"))).toEqual([]);
});

test("la oficina del Jefe propone y aprueba la semana; queda aprobada en la bóveda", async ({ page }) => {
  await page.goto("/");
  const jefe = await abrir(page, "jefe", "jefe");
  await jefe.getByRole("button", { name: "Proponer la semana" }).click();
  await expect(jefe.locator(".lo-status")).toHaveText("Plan propuesto. Revísalo y apruébalo.");
  await jefe.getByRole("button", { name: "Aprobar el plan propuesto" }).click();
  await expect(jefe.locator(".lo-status")).toHaveText("Plan aprobado y publicado en las notas diarias.");
  expect((await api<{ status: string }>(page, "/api/semana")).status).toBe("aprobado");
});

test("la oficina del Coach guarda un check-in", async ({ page }) => {
  await page.goto("/");
  const coach = await abrir(page, "coach", "coach");
  await expect(coach).toContainText("plan aprobado");
  await coach.getByRole("tab", { name: "Check-in" }).click();
  await coach.getByLabel("¿Cómo vas?").selectOption("bloqueado");
  await coach.getByRole("button", { name: "Enviar check-in" }).click();
  await expect(coach.locator(".lo-status")).toHaveText("Check-in guardado.");
  expect((await api<{ checkins: number }>(page, "/api/hoy")).checkins).toBe(1);
});

test("la captura de tu espacio llega al Inbox y un título malicioso se ve como texto", async ({ page }) => {
  const problems = vigilar(page);
  await page.goto("/");
  const tareas = await abrir(page, "tareas", "tareas");
  await expect(tareas).toContainText("<img src=x onerror=alert(1)>");
  await tareas.getByPlaceholder("Escribe una idea o tarea y pulsa Capturar").fill("Idea desde el edificio");
  await tareas.getByRole("button", { name: "Capturar" }).click();
  await expect(tareas.locator(".lo-status")).toContainText("Capturado en 00-Inbox/");
  expect(problems).toEqual([]);
});

test("el cierre se ve antes de aplicarse y se confirma", async ({ page }) => {
  await page.goto("/");
  const coach = await abrir(page, "coach", "coach");
  const confirmar = coach.getByRole("button", { name: "Confirmar cierre" });
  await coach.getByRole("tab", { name: /Cierre/ }).click();
  await expect(coach.locator(".lo-task").first()).toBeVisible();
  await expect(confirmar).toBeDisabled();
  await coach.locator(".lo-task select").first().selectOption("hecha");
  await coach.getByRole("button", { name: "Ver vista previa" }).click();
  await expect(coach.locator(".lo-status")).toHaveText("Vista previa lista: nada se cambió todavía.");
  await confirmar.click();
  await expect(coach.locator(".lo-status")).toContainText("Cierre registrado");
});

test("clic en la sala del Jefe abre su oficina; las ventanas de consulta cargan datos reales", async ({ page }) => {
  const problems = vigilar(page);
  await page.goto("/");
  await page.locator("#ui-sidebar").getByText("Oficina del Jefe").first().click();
  await expect(page.locator('dialog[data-ventana="jefe"]')).toBeVisible();
  const historial = await abrir(page, "historial", "historial");
  await expect(historial).toContainText("Diario/2026-11-02.md");
  await expect(await abrir(page, "indicadores", "indicadores")).toContainText("Racha de cierres");
  await expect(await abrir(page, "calendario", "calendario")).toContainText("Google Calendar");
  await expect(await abrir(page, "agentes", "agentes")).toContainText("Núcleo");
  await expect(await abrir(page, "propuestas", "propuestas")).toContainText("No hay propuestas pendientes.");
  expect(problems).toEqual([]);
});

const VENTANAS = ["tareas", "jefe", "coach", "agentes", "propuestas", "historial", "indicadores", "calendario"];

test("un especialista se importa, se prueba y se ejecuta desde el edificio, y su propuesta se acepta", async ({ page }) => {
  const problems = vigilar(page);
  await page.goto("/");
  const agentes = await abrir(page, "agentes", "agentes");
  for (const nombre of ["carrera", "universidad"]) {
    await agentes.getByRole("tab", { name: "Importar" }).click();
    await agentes.getByLabel("Nombre").fill(`${nombre}.yaml`);
    await agentes.getByLabel("Contenido").fill(readFileSync(resolve(HERE, `../../config/especialistas/${nombre}.yaml`), "utf-8"));
    await agentes.getByRole("button", { name: "Importar" }).click();
    await expect(agentes.locator(`[data-agente="${nombre}"]`)).toBeVisible(); // muestra la ficha recién importada
  }
  const ficha = agentes.locator("[data-agente]");
  await expect(ficha).toHaveCount(1); // una ficha cada vez
  await ficha.getByRole("button", { name: "Probar" }).click();
  await expect(ficha.locator(".lo-card__head")).toContainText("probado");
  await ficha.getByText("Ciclo de vida").click();
  await ficha.getByRole("button", { name: "Aprobar" }).click();
  await expect(ficha.locator(".lo-card__head")).toContainText("aprobado");
  await ficha.getByText("Ciclo de vida").click();
  await ficha.getByRole("button", { name: "Activar", exact: true }).click();
  await expect(ficha.locator(".lo-card__head")).toContainText("activo");
  await ficha.getByRole("button", { name: "Ejecutar" }).click();
  await expect(agentes.locator(".lo-status")).toContainText("propuestas escritas");
  await agentes.getByRole("button", { name: "Página anterior" }).click();
  await expect(agentes.locator('[data-agente="carrera"]')).toBeVisible();

  // Clic en el agente (no en la barra): solo su ficha, no la lista entera.
  await agentes.getByRole("button", { name: "Cerrar (Esc)" }).click();
  await page.locator("#ui-sidebar").getByRole("button", { name: /^Universidad, agente/ }).click();
  await expect(agentes.locator('[data-agente="universidad"]')).toBeVisible();
  await expect(agentes.locator("[data-agente]")).toHaveCount(1);

  const propuestas = await abrir(page, "propuestas", "propuestas");
  const antes = (await api<{ items: unknown[] }>(page, "/api/propuestas")).items.length;
  expect(antes).toBeGreaterThan(0);
  await propuestas.getByRole("button", { name: "Aceptar" }).first().click();
  await expect(propuestas.locator(".lo-status")).toHaveText("Propuesta aceptada.");
  expect((await api<{ items: unknown[] }>(page, "/api/propuestas")).items.length).toBe(antes - 1);
  expect(problems).toEqual([]);
});

test("ninguna ventana ocupa demasiado: compacta y paginada", async ({ page }) => {
  await page.goto("/");
  for (const id of VENTANAS) {
    const win = await abrir(page, id, id);
    await expect(win.locator(":is(.lo-item, .lo-card, .lo-stat, .lo-empty, .lo-lead):visible").first()).toBeVisible();
    const caja = await win.boundingBox();
    expect(caja?.width, id).toBeLessThanOrEqual(420);
    expect(caja?.height, id).toBeLessThanOrEqual(480);
    await win.getByRole("button", { name: "Cerrar (Esc)" }).click();
  }
  const historial = await abrir(page, "historial", "historial");
  await expect(historial.locator(".lo-item")).toHaveCount(6);
  const primera = await historial.locator(".lo-item").first().textContent();
  await historial.getByRole("button", { name: "Página siguiente" }).click();
  expect(await historial.locator(".lo-item").first().textContent()).not.toBe(primera);
});

test("ningún control se queda con el aspecto del navegador", async ({ page }) => {
  await page.goto("/");
  for (const id of VENTANAS) {
    const win = await abrir(page, id, id);
    const sinEstilo = await win.evaluate((el) =>
      [...el.querySelectorAll<HTMLElement>("select, input, textarea, button, summary, fieldset")]
        .filter((c) => {
          const s = getComputedStyle(c);
          const nativo = c.matches('select, input[type="checkbox"]') && s.appearance !== "none";
          const bordeNativo = /outset|inset|groove/.test(s.borderTopStyle);
          return nativo || bordeNativo || (c.className === "" && c.parentElement?.className === "");
        })
        .map((c) => `${c.tagName}.${c.className}`),
    );
    expect(sinEstilo, id).toEqual([]);
  }
});

test("las ventanas del edificio no tienen problemas graves de accesibilidad", async ({ page }) => {
  await page.goto("/");
  await abrir(page, "coach", "coach");
  const results = await new AxeBuilder({ page }).include(".lo-win").include(".lo-topbar").analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.nodes.map((n) => `${n.target.join(" ")} ${n.failureSummary ?? ""}`).join(" | ")}`)).toEqual([]);
});

test("cerrar sesión deja el edificio sin datos y explica cómo volver a entrar", async ({ page }) => {
  await page.goto("/");
  const logout = page.waitForResponse((r) => r.url().endsWith("/api/logout"));
  await page.locator('[data-boton="salir"]').click();
  expect((await logout).status()).toBe(204);
  expect((await page.request.get("/api/hoy")).status()).toBe(401);
  await expect(page.locator(".ui-banner")).toContainText("No hay sesión", { timeout: 10_000 });
});
