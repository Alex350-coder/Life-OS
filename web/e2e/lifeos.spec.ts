import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Flujo crítico del Hito H2 contra el backend real (bóveda temporal inventada, lunes 07:30).
// Las pruebas comparten servidor y bóveda: se ejecutan en orden.
test.describe.configure({ mode: "serial" });

const XSS = "<img src=x onerror=alert(1)>";

function failOnDialogsAndCsp(page: Page): string[] {
  const problems: string[] = [];
  page.on("dialog", (dialog) => {
    problems.push(`diálogo inesperado: ${dialog.message()}`);
    void dialog.dismiss();
  });
  page.on("console", (message) => {
    if (message.type() === "error") problems.push(message.text());
  });
  return problems;
}

async function tab(page: Page, name: string) {
  await page.getByRole("tab", { name }).click();
}

async function expectAccessible(page: Page) {
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
}

test("sin sesión no hay datos: la página pide la URL de la terminal", async ({ browser }) => {
  const context = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await context.newPage();
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("URL de un solo uso");
  const api = await page.request.get("/api/tareas");
  expect(api.status()).toBe(401);
  await context.close();
});

test("otra web no puede escribir: Origin ajeno rechazado", async ({ page }) => {
  await page.goto("/");
  const response = await page.request.post("/api/capturas", {
    data: { texto: "desde otra web" },
    headers: { Origin: "http://evil.example", "X-CSRF-Token": "x" },
  });
  expect(response.status()).toBe(403);
});

test("Hoy sin plan explica qué hacer", async ({ page }) => {
  const problems = failOnDialogsAndCsp(page);
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("propón y aprueba la semana");
  await expectAccessible(page);
  expect(problems.filter((p) => !p.includes("409"))).toEqual([]);
});

test("el Jefe propone y el propietario aprueba la semana", async ({ page }) => {
  const problems = failOnDialogsAndCsp(page);
  await page.goto("/#semana");
  await expect(page.getByRole("heading", { name: /sin plan/ })).toBeVisible();
  await page.getByRole("button", { name: "Proponer la semana" }).click();
  const jefe = page.getByRole("list", { name: "Conversación con el Jefe" });
  await expect(jefe).toContainText("Plan propuesto para 2026-W45");
  await expect(page.getByRole("heading", { name: /Semana 2026-W45 · propuesto/ })).toBeVisible();
  await page.getByRole("button", { name: "Aprobar el plan propuesto" }).click();
  await expect(jefe).toContainText("aprobado y publicado");
  await expect(page.getByRole("heading", { name: /Semana 2026-W45 · aprobado/ })).toBeVisible();
  await expect(page.getByRole("article", { name: "Lunes 2026-11-02" }).getByRole("meter")).toBeVisible();
  await expectAccessible(page);
  expect(problems).toEqual([]);
});

test("Hoy muestra el plan y el Coach responde a un bloqueo", async ({ page }) => {
  const problems = failOnDialogsAndCsp(page);
  await page.goto("/#hoy");
  await expect(page.getByRole("heading", { name: /Hoy 2026-11-02 · plan aprobado/ })).toBeVisible();
  await expect(page.getByText("Siguiente")).toBeVisible();
  await page.getByLabel("¿Cómo vas?").selectOption("bloqueado");
  await page.getByLabel("Tarea del bloque").selectOption({ index: 1 });
  await page.getByLabel("Mensaje", { exact: true }).fill("No sé por dónde empezar");
  await page.getByRole("button", { name: "Enviar check-in" }).click();
  const coach = page.getByRole("list", { name: "Conversación con el Coach" });
  await expect(coach).toContainText("Micro-paso");
  await expect(coach).toContainText("Registrado en Diario/2026-11-02.md");
  await expect(page.getByText(/Check-ins: 1/)).toBeVisible();
  await expectAccessible(page);
  expect(problems).toEqual([]);
});

test("la captura rápida va al Inbox y un título malicioso se ve como texto", async ({ page }) => {
  const problems = failOnDialogsAndCsp(page);
  await page.goto("/#tareas");
  await page.getByLabel(/Idea, tarea o recordatorio/).fill(`Idea ${XSS}`);
  await page.getByRole("button", { name: "Guardar" }).click();
  await expect(page.getByRole("status")).toContainText("00-Inbox/");
  await expect(page.getByRole("cell").filter({ hasText: XSS }).first()).toBeVisible();
  expect(await page.locator("main img").count()).toBe(0);
  await expectAccessible(page);
  expect(problems).toEqual([]);
});

test("el historial muestra las escrituras auditadas y los agentes sus permisos", async ({ page }) => {
  await page.goto("/#historial");
  const table = page.getByRole("table");
  await expect(table).toContainText("publicar_plan");
  await expect(table).toContainText("anexar_checkin");
  await expectAccessible(page);
  await tab(page, "Agentes");
  await expect(page.getByRole("heading", { name: /^Jefe/ })).toBeVisible();
  await expect(page.getByRole("heading", { name: /^Coach/ })).toBeVisible();
  await expect(page.getByText(/Dueño del plan global: jefe/)).toBeVisible();
  await expectAccessible(page);
});

test("cerrar sesión invalida la cookie en el servidor", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page.getByRole("alert")).toContainText("No hay sesión");
  expect((await page.request.get("/api/tareas")).status()).toBe(401);
});
