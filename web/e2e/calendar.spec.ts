import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

// F10-T04 (DT-27) contra el backend real sin Google configurado: la pestaña Calendario explica
// cómo conectarlo, no ofrece ninguna acción que escriba en Google y pasa axe.
test("la pestaña Calendario es de solo lectura y accesible", async ({ page }) => {
  await page.goto("/#calendario");
  const panel = page.getByRole("tabpanel", { name: "Calendario" });
  await expect(panel.getByRole("heading", { name: "Google Calendar" })).toBeVisible();
  await expect(panel.getByText(/no está configurado/)).toBeVisible();
  await expect(panel.getByRole("button")).toHaveCount(0);

  const results = await new AxeBuilder({ page }).include("main").analyze();
  const serious = results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
  expect(serious.map((v) => `${v.id}: ${v.help}`)).toEqual([]);
});
