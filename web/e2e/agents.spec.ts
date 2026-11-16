import AxeBuilder from "@axe-core/playwright";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test } from "@playwright/test";

// Fase 8 de punta a punta contra el backend real (bóveda temporal inventada, lunes 07:30):
// importar un especialista, probarlo, aprobarlo, activarlo, ejecutarlo y aceptar su propuesta.
// Se ejecuta antes que lifeos.spec.ts (orden alfabético), que termina cerrando la sesión.
test.describe.configure({ mode: "serial" });

const HERE = dirname(fileURLToPath(import.meta.url));
const TEMPLATE = readFileSync(resolve(HERE, "../../config/especialistas/universidad.yaml"), "utf-8");

test("el ciclo de un especialista termina en una propuesta aceptada", async ({ page }) => {
  const problems: string[] = [];
  page.on("dialog", (dialog) => {
    problems.push(`diálogo inesperado: ${dialog.message()}`);
    void dialog.dismiss();
  });

  await page.goto("/");
  await page.getByRole("tab", { name: "Agentes" }).click();
  await page.getByLabel("Configuración YAML").fill(TEMPLATE);
  await page.getByRole("button", { name: "Importar en borrador" }).click();
  await expect(page.getByRole("status").filter({ hasText: "v1 en borrador" })).toBeVisible();

  const card = page.getByRole("article").filter({ has: page.getByRole("heading", { name: /Universidad/ }) });
  await expect(card.getByRole("button", { name: "Ejecutar" })).toBeDisabled();
  await card.getByRole("button", { name: "Probar" }).click();
  await expect(card.getByText("Prueba sin escrituras")).toBeVisible();
  // Cada cambio recarga la tarjeta: se espera el estado nuevo antes de pulsar el siguiente botón.
  await expect(card.getByText("probado", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "Aprobar" }).click();
  await expect(card.getByText("aprobado", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "Activar", exact: true }).click();
  await expect(card.getByText("activo", { exact: true })).toBeVisible();

  await card.getByRole("button", { name: "Ejecutar" }).click();
  await expect(card.getByText(/escrito en Propuestas\/2026-W45\/universidad\.md/)).toBeVisible();

  const accept = page.getByRole("button", { name: "Aceptar 2026-W45/universidad/p1" });
  await expect(accept).toBeVisible();
  const results = await new AxeBuilder({ page }).analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
  await accept.click();
  await expect(page.getByText("Aceptada 2026-W45/universidad/p1.")).toBeVisible();

  await page.getByRole("tab", { name: "Tareas" }).click();
  await expect(page.getByText("Informe de redes").first()).toBeVisible();
  expect(problems).toEqual([]);
});
