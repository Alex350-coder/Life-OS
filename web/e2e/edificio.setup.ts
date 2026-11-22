import { expect, test as setup } from "@playwright/test";

// Sesión propia para el servidor aislado del edificio (token inventado de scripts/e2e_server.py).
const TOKEN = "token-e2e-inventado-" + "x".repeat(24);

setup("el propietario entra en el servidor del edificio", async ({ page }) => {
  await page.goto(`/auth?token=${TOKEN}`);
  await expect(page.getByRole("tab", { name: "Hoy" })).toBeVisible();
  await page.context().storageState({ path: "playwright/.auth/edificio.json" });
});
