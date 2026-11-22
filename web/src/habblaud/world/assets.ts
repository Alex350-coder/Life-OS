// Origen: Habblaud (MIT), commit faa1a, client/src/world/assets.ts. Ver LICENSE-habblaud.txt y ORIGEN.md.
// Assets opcionais gerados por IA (quadros, pôsteres e a placa da recepção).
// GET /assets/manifest.json -> { wallArt: [{ id, kind, file, w, h, tiles, variant?, framed? }], brand: { signage?, mark? } }.
// Se o manifesto não existir (ou falhar), o mundo segue só com a arte procedural.

export interface WallArtAsset {
  id: string;
  kind: 'painting' | 'poster';
  img: HTMLImageElement;
  w: number;
  h: number;
  /** Largura em tiles do lugar na parede (quadros: 2 ou 4; pôsteres: 1). */
  tiles: number;
  /** Pôster: variante procedural equivalente ('code', 'coffee'...). */
  variant?: string;
  /** Versão com moldura pronta (desenhada no lugar do quadro procedural, em tamanho nativo). */
  framed?: BrandAsset;
}

export interface BrandAsset {
  img: HTMLImageElement;
  w: number;
  h: number;
}

export interface WorldAssets {
  wallArt: WallArtAsset[];
  signage?: BrandAsset;
  mark?: BrandAsset;
}
