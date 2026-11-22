// Origen: Habblaud (MIT), commit faa1a, shared/locales/all.ts. Ver LICENSE-habblaud.txt y ORIGEN.md.
// Todos os dicionários de uma vez: o servidor, os scripts e os testes usam este módulo. No navegador, o vite.config.ts
// o troca por locales/none.ts, e o cliente carrega só o dicionário do idioma ativo (client/src/main.ts).
import type { Locale } from '../i18n';
import { ES } from './es';

export const DICTIONARIES: Partial<Record<Locale, Readonly<Record<string, string>>>> = { es: ES };
