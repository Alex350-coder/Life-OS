// Ritmo de la animación (skill motion-ui): menos fotogramas en equipos modestos y ninguno con
// movimiento reducido. Prima que la web responda sobre la suavidad (spec: PC sin GPU dedicada).
export const FRAME_MS = 1000 / 12;
export const LOW_END_FRAME_MS = 1000 / 8;
const LOW_END_CORES = 4;
const LOW_END_MEMORY_GB = 2;

interface DeviceHints {
  hardwareConcurrency?: number;
  deviceMemory?: number;
}

export function isLowEnd(hints: DeviceHints): boolean {
  if (hints.deviceMemory !== undefined) return hints.deviceMemory <= LOW_END_MEMORY_GB;
  return hints.hardwareConcurrency !== undefined && hints.hardwareConcurrency <= LOW_END_CORES;
}

export function frameInterval(hints: DeviceHints): number {
  return isLowEnd(hints) ? LOW_END_FRAME_MS : FRAME_MS;
}

export function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}
