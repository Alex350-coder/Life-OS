import { afterEach } from "vitest";

// Cada prueba empieza sin preferencias ni posiciones de ventana guardadas. Las pruebas portadas de
// Habblaud corren en el entorno node de Vitest, sin `window` (ADR-0022).
afterEach(() => {
  if (typeof window !== "undefined") window.localStorage.clear();
});
