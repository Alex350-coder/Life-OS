// Temas por estación (F9-T04, docs/specs/interfaz-grafica.md). Solo decoración: la estación sale
// de la fecha local y del hemisferio de la zona horaria del horario; no cambia ningún dato.
export type Season = "verano" | "otono" | "invierno" | "primavera";

// Zonas del hemisferio sur que puede usar el propietario: exactas, o regiones si acaban en "/".
// Las demás se tratan como del norte.
const SOUTHERN_PREFIXES = [
  "America/Lima",
  "America/Santiago",
  "America/Argentina/",
  "America/Buenos_Aires",
  "America/Sao_Paulo",
  "America/Montevideo",
  "America/Asuncion",
  "America/La_Paz",
  "Australia/",
  "Pacific/Auckland",
  "Africa/Johannesburg",
] as const;

const NORTH_BY_MONTH: readonly Season[] = [
  "invierno", "invierno", "primavera", "primavera", "primavera", "verano",
  "verano", "verano", "otono", "otono", "otono", "invierno",
];

const OPPOSITE: Record<Season, Season> = {
  verano: "invierno",
  invierno: "verano",
  otono: "primavera",
  primavera: "otono",
};

export function isSouthern(zone: string): boolean {
  return SOUTHERN_PREFIXES.some((entry) => (entry.endsWith("/") ? zone.startsWith(entry) : zone === entry));
}

// Estaciones por meses completos (dic–feb, mar–may, jun–ago, sep–nov): basta para decorar.
export function seasonAt(date: Date, zone: string): Season {
  const north = NORTH_BY_MONTH[date.getMonth()] ?? "verano";
  return isSouthern(zone) ? OPPOSITE[north] : north;
}

export const SEASON_LABEL: Record<Season, string> = {
  verano: "verano",
  otono: "otoño",
  invierno: "invierno",
  primavera: "primavera",
};
