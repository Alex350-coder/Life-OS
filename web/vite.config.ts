/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// ADR-0012: los estáticos se compilan en dist/ y los sirve el backend desde el mismo origen.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
    // Nada incrustado como data: (la CSP solo admite fuentes e imágenes del propio origen).
    assetsInlineLimit: 0,
    // Dos páginas en el mismo origen (ADR-0022): la vista clásica y el edificio.
    rollupOptions: { input: { index: "index.html", edificio: "edificio.html" } },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    coverage: {
      provider: "v8",
      include: ["src/**/*.{ts,tsx}"],
      exclude: ["src/**/*.test.{ts,tsx}", "src/test/**", "src/main.tsx", "src/lifeos-edificio/main.ts"],
      thresholds: { lines: 80, functions: 80, branches: 75, statements: 80 },
    },
  },
});
