import { expect, test as setup } from "@playwright/test";

// El token de arranque es de un solo uso: se entra una vez y se guarda la cookie de sesión.
const TOKEN = "token-e2e-inventado-" + "x".repeat(24);

setup("el propietario abre la URL de arranque y llega al edificio", async ({ page }) => {
  await page.goto(`/auth?token=${TOKEN}`);
  await expect(page.locator(".lo-brand")).toHaveText("LifeOS");
  const cookies = await page.context().cookies();
  const session = cookies.find((cookie) => cookie.name === "lifeos_session");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Strict");
  // Fuera de test-results/ (Playwright lo vacía en cada ejecución); ignorado por Git.
  await page.context().storageState({ path: "playwright/.auth/owner.json" });
});
