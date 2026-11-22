// Origen: Habblaud (MIT), commit faa1a, client/src/art/character/appearance.ts. Ver LICENSE-habblaud.txt y ORIGEN.md.
// Geração determinística de aparência a partir de uma semente (puro, sem DOM). O sorteio e as paletas moraram aqui e
// foram para shared/world/appearance-flat.ts (o mesmo sorteio serve o escritório e o Habblaud World); este módulo
// re-exporta e guarda o que é só do editor do personagem.
import { ACCESSORIES, BOTTOM_STYLES, FACIAL_HAIR, HAIR_STYLES, TOP_STYLES, type PartKey } from '../../shared/appearance';
import {
  ACC_COLORS,
  BOTTOMS,
  EYES,
  HAIR_FUN,
  HAIR_NATURAL,
  JACKET_COLORS,
  LIGHT_ACCENTS,
  SHIRT_COLORS,
  SHOES,
  SKINS,
  TIE_COLORS,
  TOP_COLORS,
  type SeedAppearance,
} from './seed';
import type { Appearance, TopStyle } from '../api';

export { appearanceFromSeed, SKINS } from './seed';
export type { BottomStyle, FacialHair } from './seed';

// O formato de shared/world/appearance-flat.ts e o da arte (art/api.ts) não podem se separar.
type Same<A, B> = [A] extends [B] ? ([B] extends [A] ? true : false) : false;
const _sameAppearance: Same<SeedAppearance, Appearance> = true;
void _sameAppearance;

/** Chave estável de uma aparência (para cache de sprites). */
export function appearanceKey(a: Appearance): string {
  return [
    a.skin, a.hair, a.hairStyle, a.eyes, a.top, a.topAccent, a.topStyle, a.bottom, a.shoes, a.accessory,
    a.accessoryColor, a.lanyard ?? '-', a.look, a.facialHair ?? '-', a.bottomStyle ?? '-',
  ].join('|');
}

// ------------------------------------------------------------------ editor do personagem

const uniq = (list: readonly string[]): readonly string[] => [...new Set(list)];
const colorsOf = (pairs: readonly (readonly [string, number])[]): string[] => pairs.map(([c]) => c);
const ACCENTS = uniq([...LIGHT_ACCENTS, ...TOP_COLORS]);

/** Cores da parte de cima e do detalhe para cada estilo: as mesmas que o sorteio usa. */
function topPalettes(style: TopStyle): { top: readonly string[]; topAccent: readonly string[] } {
  switch (style) {
    case 'shirt_tie':
      return { top: SHIRT_COLORS, topAccent: TIE_COLORS };
    case 'jacket':
      return { top: JACKET_COLORS, topAccent: ACCENTS };
    default:
      return { top: TOP_COLORS, topAccent: ACCENTS };
  }
}

export type EditorOptions = Readonly<Record<PartKey, readonly string[]>>;

/** Opções de cada peça no editor do personagem, conforme a aparência atual (as cores da roupa dependem do estilo). */
export function editorOptions(a: Appearance): EditorOptions {
  const top = topPalettes(a.topStyle);
  return {
    skin: SKINS,
    hair: [...colorsOf(HAIR_NATURAL), ...HAIR_FUN],
    hairStyle: HAIR_STYLES,
    eyes: uniq(EYES),
    top: top.top,
    topAccent: top.topAccent,
    topStyle: TOP_STYLES,
    bottom: colorsOf(BOTTOMS),
    bottomStyle: BOTTOM_STYLES,
    shoes: colorsOf(SHOES),
    accessory: ACCESSORIES,
    accessoryColor: ACC_COLORS[a.accessory],
    facialHair: FACIAL_HAIR,
  };
}

const TALL_HAIR: readonly string[] = ['afro', 'bun', 'mohawk', 'pigtails'];
const SHORT_HAIR: readonly string[] = ['bald', 'buzz'];

/** Combinação que o sorteio evita (o editor desabilita a opção): devolve o motivo, ou undefined se pode. */
export function editorConflict(a: Appearance, key: PartKey, value: string): string | undefined {
  if (key === 'accessory') {
    if ((value === 'cap' || value === 'beanie') && TALL_HAIR.includes(a.hairStyle)) return 'No combina con este peinado';
    if (value === 'bow' && SHORT_HAIR.includes(a.hairStyle)) return 'No combina con este peinado';
  }
  if (key === 'hairStyle') {
    if ((a.accessory === 'cap' || a.accessory === 'beanie') && TALL_HAIR.includes(value)) return 'No combina con la gorra ni con el gorro';
    if (a.accessory === 'bow' && SHORT_HAIR.includes(value)) return 'No combina con el lazo';
  }
  return undefined;
}

/** Depois de trocar um estilo: cores que não existem na paleta nova caem na primeira dela. */
export function fitToOptions(a: Appearance): Appearance {
  const o = editorOptions(a);
  const fit = (v: string, list: readonly string[]): string => (list.includes(v) ? v : list[0]);
  return { ...a, top: fit(a.top, o.top), topAccent: fit(a.topAccent, o.topAccent), accessoryColor: fit(a.accessoryColor, o.accessoryColor) };
}
