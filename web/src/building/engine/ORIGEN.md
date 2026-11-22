# Origen del motor del edificio (ADR-0020)

Código extraído de [Habblaud](https://github.com/marmottajr/habblaud), commit `faa1a`
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
| `art/api.ts` | `client/src/art/api.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/art.test.ts` | `client/src/art/art.test.ts` | modificado: la prueba de avisos del editor busca los textos en español |
| `art/character/appearance-move.test.ts` | `client/src/art/character/appearance-move.test.ts` | modificado: importa el sorteo desde `seed.ts` en lugar de `shared/world` |
| `art/character/appearance.ts` | `client/src/art/character/appearance.ts` | modificado: sin i18n de Habblaud (los tres avisos del editor, en español) |
| `art/character/hair.ts` | `client/src/art/character/hair.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/character/render.ts` | `client/src/art/character/render.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/core/color.ts` | `client/src/art/core/color.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/core/pixbuf.ts` | `client/src/art/core/pixbuf.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/core/sprite.ts` | `client/src/art/core/sprite.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/dynamic.ts` | `client/src/art/dynamic.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/furniture/common.ts` | `client/src/art/furniture/common.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/furniture/index.ts` | `client/src/art/furniture/index.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/furniture/kit.ts` | `client/src/art/furniture/kit.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/furniture/office.ts` | `client/src/art/furniture/office.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/furniture/wall.ts` | `client/src/art/furniture/wall.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/icons.ts` | `client/src/art/icons.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/index.ts` | `client/src/art/index.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/pixelfont.ts` | `client/src/art/pixelfont.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/seasonal.test.ts` | `client/src/art/seasonal.test.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/seasonal.ts` | `client/src/art/seasonal.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/surfaces/floor.ts` | `client/src/art/surfaces/floor.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/surfaces/rug.ts` | `client/src/art/surfaces/rug.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/surfaces/walls.ts` | `client/src/art/surfaces/walls.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/theme.ts` | `client/src/art/theme.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/constants.ts` | `client/src/world/constants.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/builder.ts` | `client/src/world/layout/builder.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/building.ts` | `client/src/world/layout/building.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/core.ts` | `client/src/world/layout/core.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/corridor.ts` | `client/src/world/layout/corridor.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/exterior.ts` | `client/src/world/layout/exterior.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/geometry.ts` | `client/src/world/layout/geometry.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/layout.test.ts` | `client/src/world/layout/layout.test.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/room.ts` | `client/src/world/layout/room.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/types.ts` | `client/src/world/layout/types.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/path/astar.test.ts` | `client/src/world/path/astar.test.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/path/astar.ts` | `client/src/world/path/astar.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/path/grid.ts` | `client/src/world/path/grid.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/appearance.ts` | `shared/appearance.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/hash.ts` | `shared/hash.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/character/seed.test.ts` | `shared/world/appearance-flat.test.ts` | modificado: solo la regresión de 20 semillas; sin las pruebas de `flattenAppearance` |
| `art/character/seed.ts` | `shared/world/appearance-flat.ts` | modificado: sin el aviso de Habblaud World ni `flattenAppearance` (lo que se subía al World); solo el sorteo puro por semilla |
