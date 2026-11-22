// Origen: LifeOS (nuevo). Sustituye los `el.innerHTML = <svg constante>` de Habblaud (ADR-0022, R-5).
// Único punto donde la interfaz portada inserta marcado: solo SVG, analizado como XML, sin scripts, sin
// manejadores `on*` y sin enlaces `javascript:`. Los iconos de Habblaud son constantes del código, nunca datos.
const SVG_NS = 'http://www.w3.org/2000/svg';
const UNSAFE_TAGS = new Set(['script', 'foreignobject', 'iframe', 'object', 'embed']);

function assertSafe(node: Element): void {
  if (UNSAFE_TAGS.has(node.localName.toLowerCase())) throw new Error(`SVG no permitido: <${node.localName}>`);
  for (const attr of Array.from(node.attributes)) {
    const name = attr.name.toLowerCase();
    if (name.startsWith('on')) throw new Error(`SVG no permitido: atributo ${attr.name}`);
    if ((name === 'href' || name === 'xlink:href') && /^\s*javascript:/i.test(attr.value)) {
      throw new Error('SVG no permitido: enlace javascript:');
    }
  }
  for (const child of Array.from(node.children)) assertSafe(child);
}

/** Pone en `el` el SVG `markup` (y solo eso). Lanza un error si no es un SVG seguro. */
export function setSvg(el: Element, markup: string): void {
  const source = markup.includes('xmlns=') ? markup : markup.replace('<svg', `<svg xmlns="${SVG_NS}"`);
  const doc = new DOMParser().parseFromString(source, 'image/svg+xml');
  const root = doc.documentElement;
  if (root.localName !== 'svg' || root.namespaceURI !== SVG_NS || doc.getElementsByTagName('parsererror').length > 0) {
    throw new Error('setSvg: el marcado no es un SVG');
  }
  assertSafe(root);
  // replaceChildren adopta el nodo del documento analizado (sin depender del `document` global).
  el.replaceChildren(root);
}
