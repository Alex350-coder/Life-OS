import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  jsxA11y.flatConfigs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { globals: { ...globals.browser } },
    plugins: { "react-hooks": reactHooks },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "no-console": "error",
      // XSS (ADR-0012): nunca HTML sin escapar.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Prohibido: el contenido de la bóveda se muestra siempre como texto.",
        },
        {
          selector: "MemberExpression[property.name=/^(innerHTML|outerHTML)$/]",
          message: "Prohibido: el contenido de la bóveda se muestra siempre como texto.",
        },
      ],
    },
  },
  {
    // Motor extraído de Habblaud (ADR-0020): se conserva su código casi literal para poder compararlo con el
    // original. Se relajan solo reglas de estilo que el original no cumple; la regla de innerHTML (XSS) sigue activa. Solo afecta a esta carpeta.
    files: ["src/habblaud/**/*.ts"],
    rules: {
      "@typescript-eslint/no-non-null-assertion": "off",
      "@typescript-eslint/no-dynamic-delete": "off",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      "prefer-const": "off",
      "no-irregular-whitespace": "off",
      // Los avisos de Habblaud por snapshots inválidos se conservan; nada de console.log.
      "no-console": ["error", { allow: ["error", "warn"] }],
    },
  },
);
