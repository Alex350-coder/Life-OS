// Origen: LifeOS (nuevo). Pruebas de ui/svg.ts: el único punto donde la interfaz portada inserta SVG.
import { describe, expect, it } from 'vitest';
import { FALLBACK_MARK, ICONS } from './icons';
import { setSvg } from './svg';

describe('setSvg', () => {
  it('inserta un icono SVG de Habblaud como nodos, sin innerHTML', () => {
    const el = document.createElement('span');
    setSvg(el, ICONS.close);
    expect(el.children).toHaveLength(1);
    expect(el.firstElementChild?.tagName.toLowerCase()).toBe('svg');
  });

  it('sustituye el contenido anterior', () => {
    const el = document.createElement('span');
    el.append('texto viejo');
    setSvg(el, ICONS.close);
    expect(el.textContent).not.toContain('texto viejo');
  });

  it('rechaza lo que no es un SVG', () => {
    const el = document.createElement('span');
    expect(() => setSvg(el, '<img src=x onerror=alert(1)>')).toThrow();
    expect(el.childNodes).toHaveLength(0);
  });

  it('rechaza scripts y manejadores de eventos dentro del SVG', () => {
    const el = document.createElement('span');
    expect(() => setSvg(el, '<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>')).toThrow();
    expect(() => setSvg(el, '<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>')).toThrow();
    expect(() => setSvg(el, '<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"></a></svg>')).toThrow();
  });

  it('todos los iconos y la marca de Habblaud pasan la validación', () => {
    for (const [name, markup] of [...Object.entries(ICONS), ['FALLBACK_MARK', FALLBACK_MARK] as const]) {
      const el = document.createElement('span');
      expect(() => setSvg(el, markup), name).not.toThrow();
    }
  });
});
