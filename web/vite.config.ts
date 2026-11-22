/// <reference types="vitest/config" />
import { defineConfig } from "vite";

// ADR-0012: los estáticos se compilan en dist/ y los sirve el backend desde el mismo origen.
// ADR-0023 (V2): la única interfaz es el edificio de Habblaud adaptado (index.html).
export default defineConfig({
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    // Nada incrustado como data: (la CSP solo admite fuentes e imágenes del propio origen).
    assetsInlineLimit: 0,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.ts"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.ts"],
      exclude: ["src/**/*.test.ts", "src/test/**", "src/lifeos-edificio/main.ts"],
      thresholds: { lines: 80, functions: 80, branches: 75, statements: 80 },
    },
  },
});
