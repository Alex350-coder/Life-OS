// Condiciones de ADR-0022 (y las cinco de ADR-0020) para la interfaz portada de Habblaud: licencia y atribución presentes, cada
// archivo con su origen documentado, sin dependencias de Habblaud World ni de red y desacoplado de LifeOS.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ENGINE = resolve(dirname(fileURLToPath(import.meta.url)), "habblaud");
const ORIGIN_HEADER = /^\/\/ Origen: (Habblaud \(MIT\), commit faa1a, \S+|LifeOS \(nuevo\))/;
// Llamadas de red (no la palabra suelta: activity.ts describe comandos como `git fetch` en sus textos).
const FORBIDDEN_CALLS = /(?<![\w'"`.])(fetch|importScripts)\s*\(|new\s+(XMLHttpRequest|WebSocket|EventSource)\b|\.sendBeacon\s*\(/;
// Módulos de red o del servicio Habblaud World. El código puro de shared/world (tipos y funciones que la
// simulación usa en su modo visita, que LifeOS no activa) sí se conserva (ADR-0022).
const FORBIDDEN_IMPORTS = /(^|\/)(net|ui\/world|world\/city|world\/visit)(\/|$)|visit-social|i18n\/|locales\/(en|fr)/;
const IMPORT_RE = /(?:import|export)\s[^'"]*?from\s+['"]([^'"]+)['"]|import\(\s*['"]([^'"]+)['"]\s*\)/g;

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    return /\.tsx?$/.test(name) ? [path] : [];
  });
}

function engineFiles(): string[] {
  return existsSync(ENGINE) ? sources(ENGINE) : [];
}

function rel(path: string): string {
  return relative(ENGINE, path).split(sep).join("/");
}

function importsOf(text: string): string[] {
  return [...text.matchAll(IMPORT_RE)].map((m) => m[1] ?? m[2] ?? "");
}

describe("interfaz de Habblaud portada (ADR-0022)", () => {
  it("conserva la licencia MIT de Habblaud", () => {
    const license = readFileSync(join(ENGINE, "LICENSE-habblaud.txt"), "utf-8");
    expect(license).toContain("MIT License");
    expect(license).toContain("Copyright (c) 2026 Márcio Junior");
  });

  it("tiene código extraído", () => {
    expect(engineFiles().length).toBeGreaterThan(20);
  });

  it("documenta el origen de cada archivo en su cabecera y en ORIGEN.md", () => {
    const origin = readFileSync(join(ENGINE, "ORIGEN.md"), "utf-8");
    for (const file of engineFiles()) {
      const text = readFileSync(file, "utf-8");
      expect(text, rel(file)).toMatch(ORIGIN_HEADER);
      expect(origin, rel(file)).toContain(`\`${rel(file)}\``);
    }
  });

  it("solo importa dentro del propio motor (sin LifeOS, React ni Habblaud World)", () => {
    for (const file of engineFiles()) {
      for (const spec of importsOf(readFileSync(file, "utf-8"))) {
        if (spec === "vitest") continue; // solo en las pruebas portadas
        expect(spec.startsWith("."), `${rel(file)} importa ${spec}`).toBe(true);
        const target = resolve(dirname(file), spec);
        expect(target.startsWith(ENGINE + sep), `${rel(file)} sale del motor con ${spec}`).toBe(true);
        expect(spec, rel(file)).not.toMatch(FORBIDDEN_IMPORTS);
      }
    }
  });

  it("no usa la red", () => {
    for (const file of engineFiles()) {
      const code = readFileSync(file, "utf-8")
        .split("\n")
        .filter((line) => !line.trimStart().startsWith("//") && !line.trimStart().startsWith("*"))
        .join("\n");
      expect(code, rel(file)).not.toMatch(FORBIDDEN_CALLS);
    }
  });
});
