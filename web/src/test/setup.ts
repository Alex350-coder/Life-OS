import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";

// Los chats se guardan en el navegador (DT-18): cada prueba empieza sin conversación previa.
afterEach(() => {
  window.localStorage.clear();
});
