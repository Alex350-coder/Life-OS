"""Extrae el motor de Habblaud (ADR-0020) a web/src/building/engine/.

Uso, desde project/:
    py -I scripts/extraer_motor_habblaud.py <clon_de_habblaud> web/src/building/engine

El clon debe estar en el commit faa1a. Solo lee del clon y escribe en el destino; no ejecuta
nada del clon. Volver a ejecutarlo regenera el motor y ORIGEN.md: se pierden los cambios hechos
a mano dentro del motor.
"""

import posixpath
import re
import shutil
import sys
from pathlib import Path

COMMIT = "faa1a"
HEADER = (
    "// Origen: Habblaud (MIT), commit {commit}, {path}. Ver LICENSE-habblaud.txt y ORIGEN.md.\n"
)
ART = "client/src/art/"
WORLD = "client/src/world/"
FIXED = {
    "client/src/world/constants": "world/constants",
    "shared/hash": "shared/hash",
    "shared/appearance": "shared/appearance",
    "shared/world/appearance-flat": "art/character/seed",
    "shared/world/appearance-flat.test": "art/character/seed.test",
}
IMPORT_RE = re.compile(r"""((?:from|import)\s*\(?\s*)(['"])(\.{1,2}/[^'"]+)\2""")
TRANSLATIONS = {
    "Não combina com este cabelo": "No combina con este peinado",
    "Não combina com o boné ou o gorro": "No combina con la gorra ni con el gorro",
    "Não combina com o laço": "No combina con el lazo",
}
COPIED = "copiado (solo cabecera de origen e importaciones relativas)"


def destination(src_noext: str) -> str | None:
    """Ruta en el motor (sin extensión) de un archivo de Habblaud, o None si queda fuera."""
    if src_noext.startswith(ART):
        return None if src_noext.endswith("/preview") else "art/" + src_noext[len(ART) :]
    for folder in ("layout/", "path/"):
        if src_noext.startswith(WORLD + folder):
            return "world/" + src_noext[len(WORLD) :]
    return FIXED.get(src_noext)


def scope(src: Path) -> list[Path]:
    files = [p for p in (src / ART).rglob("*.ts") if p.stem != "preview"]
    files += list((src / WORLD / "layout").glob("*.ts"))
    files += list((src / WORLD / "path").glob("*.ts"))
    files += [src / f"{name}.ts" for name in FIXED]
    return sorted(files)


def rewrite_imports(text: str, src_rel: str, dst_rel: str) -> str:
    def repl(match: re.Match[str]) -> str:
        spec = match.group(3)
        target = posixpath.normpath(posixpath.join(posixpath.dirname(src_rel), spec))
        mapped = destination(target)
        if mapped is None:
            raise SystemExit(f"{src_rel}: importación fuera del alcance: {spec}")
        new = posixpath.relpath(mapped, posixpath.dirname(dst_rel))
        new = new if new.startswith(".") else "./" + new
        return f"{match.group(1)}{match.group(2)}{new}{match.group(2)}"

    return IMPORT_RE.sub(repl, text)


def adapt_seed(text: str) -> str:
    start = text.index("// Aparência sorteada a partir de uma semente")
    end = text.index("/**\n * Aparência achatada para o World")
    lines = text[start:end].rstrip().splitlines(keepends=True)
    # Sin el comentario de `flattenAppearance` (lo que se subía al World).
    kept = [line for line in lines if "flattenAppearance" not in line and "No mundo a" not in line]
    return "".join(kept) + "\n"


def adapt_seed_test(text: str) -> str:
    text = text[: text.index("describe('flattenAppearance'")].rstrip() + "\n"
    text = text.replace(
        "appearanceFromSeed, flattenAppearance, type SeedAppearance",
        "appearanceFromSeed, type SeedAppearance",
    )
    text = text.replace("import { PART_KEYS, parseAppearanceParts } from '../appearance';\n", "")
    if "hash32(" not in text:
        text = text.replace("import { hash32 } from '../hash';\n", "")
    return text


def adapt_appearance(text: str) -> str:
    text = re.sub(r"import \{ tr \} from '[^']*i18n';\n", "", text)
    for original, spanish in TRANSLATIONS.items():
        text = text.replace(f"tr('{original}')", f"'{spanish}'")
    if "tr(" in text or "i18n" in text:
        raise SystemExit("appearance.ts: quedan textos con i18n de Habblaud")
    return text


def adapt_art_test(text: str) -> str:
    if text.count(".toMatch(/cabelo/)") != 2 or text.count(".toMatch(/laço/)") != 1:
        raise SystemExit("art.test.ts: cambió el número de avisos del editor")
    return text.replace(".toMatch(/cabelo/)", ".toMatch(/peinado/)").replace(
        ".toMatch(/laço/)", ".toMatch(/lazo/)"
    )


ADAPTATIONS = {
    "shared/world/appearance-flat.ts": (
        adapt_seed,
        "modificado: sin el aviso de Habblaud World ni `flattenAppearance` (lo que se subía "
        "al World); solo el sorteo puro por semilla",
    ),
    "shared/world/appearance-flat.test.ts": (
        adapt_seed_test,
        "modificado: solo la regresión de 20 semillas; sin las pruebas de `flattenAppearance`",
    ),
    "client/src/art/character/appearance.ts": (
        adapt_appearance,
        "modificado: sin i18n de Habblaud (los tres avisos del editor, en español)",
    ),
    "client/src/art/art.test.ts": (
        adapt_art_test,
        "modificado: la prueba de avisos del editor busca los textos en español",
    ),
    "client/src/art/character/appearance-move.test.ts": (
        lambda text: text,
        "modificado: importa el sorteo desde `seed.ts` en lugar de `shared/world`",
    ),
}

ORIGIN_INTRO = f"""# Origen del motor del edificio (ADR-0020)

Código extraído de [Habblaud](https://github.com/marmottajr/habblaud), commit `{COMMIT}`
(2026-10-16), licencia MIT (`LICENSE-habblaud.txt`, copia literal). Cada archivo empieza con
una cabecera de origen.

Estados: **copiado** (sin cambios de lógica; solo la cabecera y las rutas de importación),
**modificado** (con el motivo) o **nuevo** (escrito para LifeOS). Esta tabla se actualiza en
cada commit que toque el motor.

No se copió: `art/preview.ts`, las PNG de `client/public/`, `world/sim`, `world/render`,
`world/social`, `world/city`, `world/visit`, `ui/`, `server/`, `mod/` ni el resto de
`shared/world/`.

Configuración propia: `web/tsconfig.engine.json` compila esta carpeta con el `strict` de
Habblaud (sin `noUncheckedIndexedAccess` ni `exactOptionalPropertyTypes`, que Habblaud no usa).
El resto de la web la consume por referencia de proyecto con su configuración completa. En
`eslint.config.js`, solo esta carpeta admite aserciones no nulas. Así el código queda casi
literal y se puede comparar con el original.

Regenerar, desde `project/`:
`py -I scripts/extraer_motor_habblaud.py <clon_de_habblaud> web/src/building/engine`

| Archivo del motor | Original en Habblaud | Estado |
|---|---|---|
"""


def main(src: Path, dst: Path) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src / "LICENSE", dst / "LICENSE-habblaud.txt")
    rows = []
    for path in scope(src):
        src_rel = path.relative_to(src).as_posix()
        mapped = destination(src_rel.removesuffix(".ts"))
        if mapped is None:
            raise SystemExit(f"{src_rel}: fuera del alcance")
        dst_rel = mapped + ".ts"
        adapt, status = ADAPTATIONS.get(src_rel, (lambda text: text, COPIED))
        text = rewrite_imports(adapt(path.read_text(encoding="utf-8")), src_rel, dst_rel)
        out = dst / dst_rel
        out.parent.mkdir(parents=True, exist_ok=True)
        header = HEADER.format(commit=COMMIT, path=src_rel)
        out.write_text(header + text, encoding="utf-8", newline="\n")
        rows.append(f"| `{dst_rel}` | `{src_rel}` | {status} |")
    origin = ORIGIN_INTRO + "\n".join(rows) + "\n"
    (dst / "ORIGEN.md").write_text(origin, encoding="utf-8", newline="\n")
    print(f"{len(rows)} archivos extraídos")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]))
