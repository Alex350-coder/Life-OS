import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Los chats se guardan en el navegador (DT-18): cada prueba empieza sin conversación previa. Las pruebas
// portadas de Habblaud corren en el entorno node de Vitest, sin `window` (ADR-0022).
afterEach(() => {
  if (typeof window !== "undefined") window.localStorage.clear();
});
