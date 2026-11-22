// Formatos compartidos por las ventanas del edificio (solo presentación).

/** 90 → «1 h 30 min»; 45 → «45 min»; 120 → «2 h». */
export function horas(minutos: number): string {
  const h = Math.floor(minutos / 60);
  const m = minutos % 60;
  if (h === 0) return `${m} min`;
  return m === 0 ? `${h} h` : `${h} h ${m} min`;
}

/** Fecha y hora ISO → «2026-11-02 07:30» (sin zona: la del servidor ya viene en el texto). */
export function momento(iso: string): string {
  return iso.replace("T", " ").slice(0, 16);
}
