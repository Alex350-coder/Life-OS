# Origen de la interfaz de Habblaud portada (ADR-0022)

Código de [Habblaud](https://github.com/marmottajr/habblaud), commit `faa1a` (2026-10-16),
licencia MIT (`LICENSE-habblaud.txt`, copia literal). Cada archivo de código empieza con una
cabecera de origen.

Estados: **copiado** (sin cambios de lógica; solo la cabecera y las rutas de importación),
**modificado** (con el motivo) o **nuevo** (escrito para LifeOS). Lo de LifeOS que usa esta
carpeta vive fuera, en `web/src/lifeos-edificio/`; aquí solo está `world/source.ts`, la interfaz que implementa.

Cortado (no se copia; ver `docs/specs/adaptacion-habblaud.md` §3): `client/src/net/store` (red: SSE del servidor de Habblaud (lo sustituye LifeosStore)), `client/src/ui/permission` (permisos de Claude Code), `shared/locales/en` (solo español), `shared/locales/fr` (solo español), y todo lo que solo usan los módulos de la categoría 4 (World, terminal, mensajería,
Codex, GitHub, sonido de la UI, timelapse, editor de personajes y la marca de Habblaud).

Configuración propia: `web/tsconfig.engine.json` compila esta carpeta con el `strict` de
Habblaud (sin `noUncheckedIndexedAccess` ni `exactOptionalPropertyTypes`). El resto de la web la
consume por referencia de proyecto con su configuración completa. En `eslint.config.js`, solo
esta carpeta admite las reglas que el código original no cumple.

Pruebas de Habblaud no portadas: `shared/world/floor-labels.test.ts` (exige los 4 idiomas de Habblaud World; LifeOS solo porta el español).

Regenerar, desde `project/`:
`py -I scripts/extraer_motor_habblaud.py <clon_de_habblaud> web/src/habblaud`

| Archivo | Original en Habblaud | Estado |
|---|---|---|
| `art/api.ts` | `client/src/art/api.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/character/appearance.ts` | `client/src/art/character/appearance.ts` | copiado (solo cabecera de origen e importaciones relativas) |
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
| `art/seasonal.ts` | `client/src/art/seasonal.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/surfaces/floor.ts` | `client/src/art/surfaces/floor.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/surfaces/rug.ts` | `client/src/art/surfaces/rug.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/surfaces/walls.ts` | `client/src/art/surfaces/walls.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/theme.ts` | `client/src/art/theme.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `audio/scheduler.ts` | `client/src/audio/scheduler.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/agentorder.ts` | `client/src/ui/agentorder.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/avatar.ts` | `client/src/ui/avatar.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/context.ts` | `client/src/ui/context.ts` | modificado: el contexto usa `SnapshotSource` (lo implementa el store de LifeOS) |
| `ui/dom.ts` | `client/src/ui/dom.ts` | modificado: SVG con `setSvg` (ui/svg.ts) en lugar de `innerHTML` |
| `ui/feed.ts` | `client/src/ui/feed.ts` | modificado: SVG con `setSvg` (ui/svg.ts) en lugar de `innerHTML` |
| `ui/format.ts` | `client/src/ui/format.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/hovertip.ts` | `client/src/ui/hovertip.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/icons.ts` | `client/src/ui/icons.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/model.ts` | `client/src/ui/model.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/movable.ts` | `client/src/ui/movable.ts` | modificado: SVG con `setSvg` (ui/svg.ts) en lugar de `innerHTML` |
| `ui/overlays.ts` | `client/src/ui/overlays.ts` | modificado: sin `EmptyState` (pedía abrir Claude Code y ofrecía una demostración con datos ficticios); SVG con `setSvg` (ui/svg.ts) en lugar de `innerHTML` |
| `ui/prefs.ts` | `client/src/ui/prefs.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/provider.ts` | `client/src/ui/provider.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/roomorder.ts` | `client/src/ui/roomorder.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/rows.ts` | `client/src/ui/rows.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/sidebar.ts` | `client/src/ui/sidebar.ts` | modificado: SVG con `setSvg` (ui/svg.ts) en lugar de `innerHTML` |
| `ui/toasts.ts` | `client/src/ui/toasts.ts` | modificado: sin permisos; sin el botón «Responder» de permisos de Claude Code |
| `ui/viewport.ts` | `client/src/ui/viewport.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/widgets.ts` | `client/src/ui/widgets.ts` | modificado: SVG con `setSvg` (ui/svg.ts) en lugar de `innerHTML` |
| `world/api.ts` | `client/src/world/api.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/assets.ts` | `client/src/world/assets.ts` | modificado: solo los tipos; sin `loadWorldAssets` (red) |
| `world/camera.ts` | `client/src/world/camera.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/constants.ts` | `client/src/world/constants.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/debug.ts` | `client/src/world/debug.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/index.ts` | `client/src/world/index.ts` | modificado: el mundo lee de `SnapshotSource` (LifeOS) en lugar del store SSE de Habblaud; sin cargar assets PNG por red |
| `world/input.ts` | `client/src/world/input.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/builder.ts` | `client/src/world/layout/builder.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/building.ts` | `client/src/world/layout/building.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/core.ts` | `client/src/world/layout/core.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/corridor.ts` | `client/src/world/layout/corridor.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/exterior.ts` | `client/src/world/layout/exterior.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/geometry.ts` | `client/src/world/layout/geometry.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/room.ts` | `client/src/world/layout/room.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/layout/types.ts` | `client/src/world/layout/types.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/path/astar.ts` | `client/src/world/path/astar.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/path/grid.ts` | `client/src/world/path/grid.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/playback.ts` | `client/src/world/playback.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/anim.ts` | `client/src/world/render/anim.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/daylight.ts` | `client/src/world/render/daylight.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/github-fx.ts` | `client/src/world/render/github-fx.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/lighting.ts` | `client/src/world/render/lighting.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/overlay.ts` | `client/src/world/render/overlay.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/particles.ts` | `client/src/world/render/particles.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/props.ts` | `client/src/world/render/props.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/renderer.ts` | `client/src/world/render/renderer.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/scene.ts` | `client/src/world/render/scene.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/seasonal.ts` | `client/src/world/render/seasonal.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/render/shell-sprites.ts` | `client/src/world/render/shell-sprites.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/behavior.ts` | `client/src/world/sim/behavior.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/character.ts` | `client/src/world/sim/character.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/elevator.ts` | `client/src/world/sim/elevator.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/github.ts` | `client/src/world/sim/github.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/room-state.ts` | `client/src/world/sim/room-state.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/shell.ts` | `client/src/world/sim/shell.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/sim.ts` | `client/src/world/sim/sim.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/spots.ts` | `client/src/world/sim/spots.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/steps.ts` | `client/src/world/sim/steps.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sim/visitor.ts` | `client/src/world/sim/visitor.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/social/gathering.ts` | `client/src/world/social/gathering.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/social/lines.ts` | `client/src/world/social/lines.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/social/persona.ts` | `client/src/world/social/persona.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/social/social.ts` | `client/src/world/social/social.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/social/wallet.ts` | `client/src/world/social/wallet.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `world/sound-cues.ts` | `client/src/world/sound-cues.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/activity.ts` | `shared/activity.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/appearance.ts` | `shared/appearance.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/hash.ts` | `shared/hash.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/i18n.ts` | `shared/i18n.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/locales/all.ts` | `shared/locales/all.ts` | modificado: solo el diccionario español |
| `shared/locales/es.ts` | `shared/locales/es.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/types.ts` | `shared/types.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/world/api.ts` | `shared/world/api.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/world/appearance-flat.ts` | `shared/world/appearance-flat.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/world/floor-labels.ts` | `shared/world/floor-labels.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/world/floor-names.ts` | `shared/world/floor-names.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/world/office-from-world.ts` | `shared/world/office-from-world.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/world/protocol.ts` | `shared/world/protocol.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `shared/world/traits.ts` | `shared/world/traits.ts` | copiado (solo cabecera de origen e importaciones relativas) |
| `art/art.test.ts` | `client/src/art/art.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `art/character/appearance-move.test.ts` | `client/src/art/character/appearance-move.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `art/seasonal.test.ts` | `client/src/art/seasonal.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `audio/scheduler.test.ts` | `client/src/audio/scheduler.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `ui/feed.test.ts` | `client/src/ui/feed.test.ts` | modificado: el DOM falso de la prueba implementa `replaceChildren` (lo usa `setSvg`); prueba: declara el entorno node de Vitest, como en Habblaud |
| `ui/format.test.ts` | `client/src/ui/format.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `ui/model.test.ts` | `client/src/ui/model.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `ui/prefs.test.ts` | `client/src/ui/prefs.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `ui/provider.test.ts` | `client/src/ui/provider.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `ui/viewport.test.ts` | `client/src/ui/viewport.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/input.test.ts` | `client/src/world/input.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/layout/layout.test.ts` | `client/src/world/layout/layout.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/path/astar.test.ts` | `client/src/world/path/astar.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/render/daylight.test.ts` | `client/src/world/render/daylight.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/render/overlay.test.ts` | `client/src/world/render/overlay.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/render/particles.test.ts` | `client/src/world/render/particles.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/sim/github.test.ts` | `client/src/world/sim/github.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/sim/shell.test.ts` | `client/src/world/sim/shell.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/sim/sim-pure.test.ts` | `client/src/world/sim/sim-pure.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/sim/sim.test.ts` | `client/src/world/sim/sim.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/social/social-pure.test.ts` | `client/src/world/social/social-pure.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/social/social.test.ts` | `client/src/world/social/social.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `world/theme.test.ts` | `client/src/world/theme.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `shared/activity.test.ts` | `shared/activity.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `shared/appearance.test.ts` | `shared/appearance.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `shared/world/api.test.ts` | `shared/world/api.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `shared/world/appearance-flat.test.ts` | `shared/world/appearance-flat.test.ts` | modificado: prueba: declara el entorno node de Vitest, como en Habblaud |
| `ui/styles.css` | `client/src/ui/styles.css` | copiado (solo cabecera de origen e importaciones relativas) |
| `ui/svg.test.ts` | — | nuevo (LifeOS) |
| `ui/svg.ts` | — | nuevo (LifeOS) |
| `world/source.ts` | — | nuevo (LifeOS) |
