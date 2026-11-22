import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

// Edificio (F11, ADR-0022/0023): la interfaz de LifeOS contra el backend real (bóveda temporal inventada,
// lunes 2026-11-02 07:30). Se usa LifeOS entero desde la interfaz de Habblaud y se comprueba con la API
// que cada acción persiste. Serie: cada prueba parte del estado que deja la anterior.
test.describe.configure({ mode: "serial" });

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
  await coach.locator("select").first().selectOption("bloqueado");
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
  await expect(historial).toContainText("publicar plan");
  await expect(await abrir(page, "indicadores", "indicadores")).toContainText("racha de cierres");
  await expect(await abrir(page, "calendario", "calendario")).toContainText("Google Calendar");
  await expect(await abrir(page, "agentes", "agentes")).toContainText("Agentes del núcleo");
  await expect(await abrir(page, "propuestas", "propuestas")).toContainText("No hay propuestas pendientes.");
  expect(problems).toEqual([]);
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
