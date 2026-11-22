"""Porta el cliente de Habblaud (ADR-0022) a web/src/habblaud/.

Uso, desde project/:
    py -I scripts/extraer_motor_habblaud.py <clon_de_habblaud> web/src/habblaud

El clon debe estar en el commit faa1a. Solo lee del clon y escribe en el destino; no ejecuta
nada del clon. Copia el cierre de dependencias de las raíces (el mundo y los componentes base de
la interfaz), no sigue los módulos cortados (red, servidor de Habblaud, terminal) y aplica los
parches de PATCHES, cada uno comprobado. Volver a ejecutarlo regenera la carpeta y ORIGEN.md:
los archivos `nuevo` de LifeOS (NEW_FILES) no se tocan.
"""

import posixpath
import re
import shutil
import sys
from collections.abc import Callable
from pathlib import Path

COMMIT = "faa1a"
HEADER = (
    "// Origen: Habblaud (MIT), commit {commit}, {path}. Ver LICENSE-habblaud.txt y ORIGEN.md.\n"
)
CLIENT = "client/src/"
ROOTS = [
    "client/src/world/index.ts",
    *(
        f"client/src/ui/{name}.ts"
        for name in [
            "dom",
            "icons",
            "widgets",
            "rows",
            "movable",
            "viewport",
            "hovertip",
            "toasts",
            "overlays",
            "feed",
            "sidebar",
            "model",
            "avatar",
            "format",
            "prefs",
            "context",
        ]
    ),
    "shared/i18n.ts",
]
ASSETS = ["client/src/ui/styles.css"]
# Módulos que no se siguen. Quien los importa recibe un parche (PATCHES) o un archivo nuevo.
CUT = {
    "client/src/net/store": "red: SSE del servidor de Habblaud (lo sustituye LifeosStore)",
    "client/src/ui/permission": "permisos de Claude Code",
    "shared/locales/en": "solo español",
    "shared/locales/fr": "solo español",
}
# Archivos escritos para LifeOS dentro de la carpeta portada: el extractor no los toca.
NEW_FILES = {"world/source.ts", "ui/svg.ts", "ui/svg.test.ts"}
# innerHTML con SVG constantes → setSvg (ui/svg.ts), con el número exacto de usos por archivo.
SVG_FILES = {
    "client/src/ui/dom.ts": 1,
    "client/src/ui/feed.ts": 1,
    "client/src/ui/movable.ts": 1,
    "client/src/ui/overlays.ts": 1,
    "client/src/ui/sidebar.ts": 2,
    "client/src/ui/widgets.ts": 1,
}
INNER_RE = re.compile(r"([\w.]+)\.innerHTML = ([^;\n]+);")
# Pruebas de Habblaud que no aplican tras la adaptación (con el motivo, también en ORIGEN.md).
SKIPPED_TESTS = {
    "shared/world/floor-labels.test.ts": "exige los 4 idiomas de Habblaud World; LifeOS solo porta el español",
}
# Pruebas que necesitan el DOM real (jsdom) porque ahora usan setSvg (DOMParser).
JSDOM_TESTS = {"client/src/ui/feed.test.ts": "jsdom"}
IMPORT_RE = re.compile(r"""((?:from|import)\s*\(?\s*)(['"])(\.{1,2}/[^'"]+)\2""")
COPIED = "copiado (solo cabecera de origen e importaciones relativas)"


def destination(src: str) -> str:
    """Ruta dentro de habblaud/: client/src/x → x; shared/x → shared/x."""
    return src[len(CLIENT) :] if src.startswith(CLIENT) else src


def resolve(root: Path, base: str, spec: str) -> str | None:
    target = posixpath.normpath(posixpath.join(posixpath.dirname(base), spec))
    for cand in (target + ".ts", target + "/index.ts", target):
        if (root / cand).is_file():
            return cand
    return None


def closure(root: Path) -> list[str]:
    seen: set[str] = set()
    stack = list(ROOTS)
    while stack:
        rel = stack.pop()
        if rel in seen:
            continue
        seen.add(rel)
        text = patched(rel, (root / rel).read_text(encoding="utf-8"))
        for spec in IMPORT_RE.findall(text):
            dep = resolve(root, rel, spec[2])
            if dep and not dep.endswith(".css") and dep.removesuffix(".ts") not in CUT:
                stack.append(dep)
    return sorted(seen)


def replace_once(old: str, new: str) -> Callable[[str], str]:
    def apply(text: str) -> str:
        if text.count(old) != 1:
            raise SystemExit(f"parche no aplicable ({text.count(old)} coincidencias): {old[:60]!r}")
        return text.replace(old, new)

    return apply


def cut_between(start: str, end: str) -> Callable[[str], str]:
    """Quita el bloque que va desde `start` hasta justo antes de `end` (ambos únicos)."""

    def apply(text: str) -> str:
        if text.count(start) != 1 or text.count(end) != 1:
            raise SystemExit(f"corte no aplicable: {start[:60]!r}")
        return text[: text.index(start)] + text[text.index(end) :]

    return apply


def assets_types(text: str) -> str:
    # Solo los tipos: loadWorldAssets pedía /assets/manifest.json (red y PNG que no se copian).
    return text[: text.index("interface ManifestFile")].rstrip() + "\n"


PATCHES: dict[str, list[tuple[Callable[[str], str], str]]] = {
    "client/src/world/index.ts": [
        (
            replace_once(
                "import type { OfficeStore } from '../net/store';",
                "import type { SnapshotSource as OfficeStore } from './source';",
            ),
            "el mundo lee de `SnapshotSource` (LifeOS) en lugar del store SSE de Habblaud",
        ),
        (
            replace_once(
                "import { loadWorldAssets, type WorldAssets } from './assets';",
                "import type { WorldAssets } from './assets';",
            ),
            "sin cargar assets PNG por red",
        ),
        (
            replace_once(
                "  void loadWorldAssets(abort.signal).then((a) => {\n    if (!a) return;\n"
                "    assets = a;\n    renderer.setAssets(a);\n  });\n",
                "",
            ),
            "",
        ),
    ],
    "client/src/world/assets.ts": [(assets_types, "solo los tipos; sin `loadWorldAssets` (red)")],
    "client/src/ui/context.ts": [
        (
            replace_once(
                "import type { OfficeStore } from '../net/store';",
                "import type { SnapshotSource as OfficeStore } from '../world/source';",
            ),
            "el contexto usa `SnapshotSource` (lo implementa el store de LifeOS)",
        ),
    ],
    "client/src/ui/toasts.ts": [
        (replace_once("import { focusPermission } from './permission';\n", ""), "sin permisos"),
        (
            replace_once(
                "    if (n.level === 'alert' && n.agentId) {\n      const id = n.agentId;\n"
                "      toast.answer = h('button', { class: 'ui-toast__answer', type: 'button', "
                "text: tr('Responder'), title: tr('Responder pelo escritório (aprovar, recusar ou "
                "responder a pergunta)'), hidden: !agent?.permission, on: { click: () => "
                "focusPermission(this.ctx, id) } });\n      el.append(toast.answer);\n    }\n",
                "",
            ),
            "sin el botón «Responder» de permisos de Claude Code",
        ),
    ],
    "client/src/ui/overlays.ts": [
        (
            cut_between(
                "export class EmptyState implements UiComponent {", "export class ConnectionBanner"
            ),
            "sin `EmptyState` (pedía abrir Claude Code y ofrecía una demostración con datos ficticios)",
        ),
        (replace_once("import { emptyOfficeHint } from './provider';\n", ""), ""),
    ],
    "client/src/ui/feed.test.ts": [
        (
            replace_once(
                "  append(...children: Element[]): void { for (const child of children) this.insertBefore(child, null); }\n",
                "  append(...children: Element[]): void { for (const child of children) this.insertBefore(child, null); }\n"
                "  replaceChildren(...nodes: unknown[]): void { this.children = nodes as Element[]; }\n",
            ),
            "el DOM falso de la prueba implementa `replaceChildren` (lo usa `setSvg`)",
        ),
    ],
    "shared/locales/all.ts": [
        (
            replace_once(
                "import { EN } from './en';\nimport { ES } from './es';\nimport { FR } from './fr';\n",
                "import { ES } from './es';\n",
            ),
            "solo el diccionario español",
        ),
        (replace_once("{ en: EN, es: ES, fr: FR }", "{ es: ES }"), ""),
    ],
}


def svg_patch(rel: str, text: str) -> str:
    expected = SVG_FILES[rel]
    text, count = INNER_RE.subn(r"setSvg(\1, \2);", text)
    if count != expected or "innerHTML" in text:
        raise SystemExit(f"{rel}: {count} innerHTML (esperados {expected})")
    first = text.find("import ")
    first = 0 if first < 0 else first
    return text[:first] + "import { setSvg } from './svg';\n" + text[first:]


def patched(rel: str, text: str) -> str:
    for apply, _ in PATCHES.get(rel, []):
        text = apply(text)
    if rel in SVG_FILES:
        text = svg_patch(rel, text)
    return text


def rewrite_imports(text: str, src_rel: str) -> str:
    dst_dir = posixpath.dirname(destination(src_rel))

    def repl(match: re.Match[str]) -> str:
        spec = match.group(3)
        target = posixpath.normpath(posixpath.join(posixpath.dirname(src_rel), spec))
        new = posixpath.relpath(destination(target), dst_dir)
        new = new if new.startswith(".") else "./" + new
        return f"{match.group(1)}{match.group(2)}{new}{match.group(2)}"

    return IMPORT_RE.sub(repl, text)


def status(rel: str) -> str:
    notes = [note for _, note in PATCHES.get(rel, []) if note]
    if rel in SVG_FILES:
        notes.append("SVG con `setSvg` (ui/svg.ts) en lugar de `innerHTML`")
    if rel.endswith(".test.ts"):
        notes.append("prueba: declara el entorno node de Vitest, como en Habblaud")
    return "modificado: " + "; ".join(notes) if notes else COPIED


ORIGIN_INTRO = f"""# Origen de la interfaz de Habblaud portada (ADR-0022)

Código de [Habblaud](https://github.com/marmottajr/habblaud), commit `{COMMIT}` (2026-10-16),
licencia MIT (`LICENSE-habblaud.txt`, copia literal). Cada archivo de código empieza con una
cabecera de origen.

Estados: **copiado** (sin cambios de lógica; solo la cabecera y las rutas de importación),
**modificado** (con el motivo) o **nuevo** (escrito para LifeOS). Lo de LifeOS que usa esta
carpeta vive fuera, en `web/src/lifeos-edificio/`; aquí solo está `world/source.ts`, la interfaz que implementa.

Cortado (no se copia; ver `docs/specs/adaptacion-habblaud.md` §3): {
    ", ".join(f"`{k}` ({v})" for k, v in CUT.items())
}, y todo lo que solo usan los módulos de la categoría 4 (World, terminal, mensajería,
Codex, GitHub, sonido de la UI, timelapse, editor de personajes y la marca de Habblaud).

Configuración propia: `web/tsconfig.engine.json` compila esta carpeta con el `strict` de
Habblaud (sin `noUncheckedIndexedAccess` ni `exactOptionalPropertyTypes`). El resto de la web la
consume por referencia de proyecto con su configuración completa. En `eslint.config.js`, solo
esta carpeta admite las reglas que el código original no cumple.

Pruebas de Habblaud no portadas: {", ".join(f"`{k}` ({v})" for k, v in SKIPPED_TESTS.items())}.

Regenerar, desde `project/`:
`py -I scripts/extraer_motor_habblaud.py <clon_de_habblaud> web/src/habblaud`

| Archivo | Original en Habblaud | Estado |
|---|---|---|
"""


def ported_tests(root: Path) -> list[str]:
    """Pruebas de Habblaud cuyas importaciones están todas dentro de lo portado."""
    kept = set(closure(root))
    tests = []
    for path in sorted(
        [*(root / "client/src").rglob("*.test.ts"), *(root / "shared").rglob("*.test.ts")]
    ):
        rel = path.relative_to(root).as_posix()
        text = patched(rel, path.read_text(encoding="utf-8"))
        deps = [resolve(root, rel, spec[2]) for spec in IMPORT_RE.findall(text)]
        if all(dep in kept for dep in deps) and rel not in SKIPPED_TESTS:
            tests.append(rel)
    return tests


def build(src: Path) -> tuple[dict[str, str], list[str]]:
    """Calcula todo en memoria: si un parche falla, la carpeta de destino no se toca."""
    outputs: dict[str, str] = {}
    rows = []
    for rel in closure(src) + ported_tests(src) + ASSETS:
        text = (src / rel).read_text(encoding="utf-8")
        if rel.endswith(".ts"):
            text = HEADER.format(commit=COMMIT, path=rel) + rewrite_imports(patched(rel, text), rel)
            if rel.endswith(".test.ts"):
                # Habblaud ejecuta sus pruebas en el entorno node de Vitest (su vitest.config.ts).
                env = JSDOM_TESTS.get(rel, "node")
                text = text.replace("\n", f"\n// @vitest-environment {env}\n", 1)
        else:
            text = f"/* Origen: Habblaud (MIT), commit {COMMIT}, {rel}. */\n" + text
        outputs[destination(rel)] = text
        rows.append(f"| `{destination(rel)}` | `{rel}` | {status(rel)} |")
    return outputs, rows


def main(src: Path, dst: Path) -> None:
    outputs, rows = build(src)
    keep = {p: (dst / p).read_bytes() for p in NEW_FILES if (dst / p).exists()}
    missing = sorted(NEW_FILES - keep.keys())
    if missing and dst.exists() and any(dst.iterdir()):
        raise SystemExit(f"faltan archivos nuevos de LifeOS en el destino: {missing}")
    # Se vacía la carpeta (no se borra): en Windows, una terminal abierta dentro la bloquearía.
    dst.mkdir(parents=True, exist_ok=True)
    for child in dst.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copyfile(src / "LICENSE", dst / "LICENSE-habblaud.txt")
    for rel, text in outputs.items():
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_text(text, encoding="utf-8", newline="\n")
    for rel, data in sorted(keep.items()):
        (dst / rel).parent.mkdir(parents=True, exist_ok=True)
        (dst / rel).write_bytes(data)
        rows.append(f"| `{rel}` | — | nuevo (LifeOS) |")
    (dst / "ORIGEN.md").write_text(ORIGIN_INTRO + "\n".join(rows) + "\n", encoding="utf-8")
    print(f"{len(rows)} archivos")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
