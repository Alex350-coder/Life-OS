import { defineConfig, devices } from "@playwright/test";

// Punta a punta contra el backend real (scripts/e2e_server.py): bóveda temporal inventada,
// reloj fijo y token de arranque conocido. Requiere `npm run build` antes.
const PORT = 8799;
export const BASE_URL = `http://127.0.0.1:${PORT}`;

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
      use: { ...devices["Desktop Chrome"], storageState: "test-results/.auth/owner.json" },
      dependencies: ["setup"],
      testMatch: /.*\.spec\.ts/,
    },
  ],
  webServer: {
    command: "uv run python scripts/e2e_server.py",
    cwd: "..",
    url: `${BASE_URL}/`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
});
