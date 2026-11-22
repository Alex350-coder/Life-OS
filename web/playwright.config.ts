import { defineConfig, devices } from "@playwright/test";

// Punta a punta contra el backend real (scripts/e2e_server.py): bóveda temporal inventada,
// reloj fijo y token de arranque conocido. Requiere `npm run build` antes.
// El edificio (F11) usa su propio servidor y su propia bóveda: sus acciones no alteran el estado
// del que dependen, en orden, las pruebas de la vista clásica.
const PORT = 8799;
const EDIFICIO_PORT = 8798;
export const BASE_URL = `http://127.0.0.1:${PORT}`;
const EDIFICIO_URL = `http://127.0.0.1:${EDIFICIO_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: BASE_URL, trace: "retain-on-failure" },
  projects: [
    { name: "setup", testMatch: /auth\.setup\.ts/ },
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"], storageState: "playwright/.auth/owner.json" },
      dependencies: ["setup"],
      testMatch: /.*\.spec\.ts/,
      testIgnore: /edificio\.spec\.ts/,
    },
    { name: "setup-edificio", testMatch: /edificio\.setup\.ts/, use: { baseURL: EDIFICIO_URL } },
    {
      name: "edificio",
      use: { ...devices["Desktop Chrome"], baseURL: EDIFICIO_URL, storageState: "playwright/.auth/edificio.json" },
      dependencies: ["setup-edificio"],
      testMatch: /edificio\.spec\.ts/,
    },
  ],
  webServer: [
    {
      command: "uv run python scripts/e2e_server.py",
      cwd: "..",
      url: `${BASE_URL}/`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: `uv run python scripts/e2e_server.py ${EDIFICIO_PORT}`,
      cwd: "..",
      url: `${EDIFICIO_URL}/edificio.html`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
