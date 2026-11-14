// Dibujo de la oficina en un canvas 2D: pixel art generado por código (inspirado en Habblaud),
// sin imágenes externas ni dependencias (CSP de ADR-0013). Paleta de DESIGN.md.
import type { Character, Light, Pose, Scene } from "./scene";

export const WIDTH = 640;
export const HEIGHT = 360;
const PX = 4; // tamaño del "píxel" de los sprites
const WALL_BOTTOM = 130;
const DESK_Y = 210;
const LOUNGE_X = 520;

const COLORS = {
  void: "#0e0e0e",
  carbon: "#1c1c1c",
  graphite: "#2b2b2b",
  smoke: "#3f3f3f",
  edge: "#2f3336",
  snow: "#ffffff",
  ash: "#d3d3d3",
  mist: "#a1a1aa",
  cyan: "#6fe2ff",
  amber: "#ffba6a",
  lavender: "#d8d3ff",
  skin: "#e8b48c",
  wood: "#5a4632",
};

const SKY: Record<Light, [string, string]> = {
  noche: ["#0b1430", "#1c2147"],
  amanecer: ["#f4c587", "#d6d3e9"],
  dia: ["#99d2ec", "#d6d3e9"],
  atardecer: ["#f4c587", "#99d2ec"],
};

const SHIRT: Record<string, string> = { tu: COLORS.snow, jefe: COLORS.amber, coach: COLORS.cyan };
const HAIR = ["#2b1d14", "#4a3424", "#1c1c1c", "#8a5a2b", "#d8d3ff"];

export interface HitBox {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

function deskX(desk: number): number {
  // Puesto 0 (tú) a la izquierda; Jefe, Coach y especialistas a la derecha, en filas de 4.
  const column = desk % 4;
  return 40 + column * 115;
}

function deskY(desk: number): number {
  return DESK_Y + Math.floor(desk / 4) * 70;
}

function rect(ctx: CanvasRenderingContext2D, color: string, x: number, y: number, w: number, h: number) {
  ctx.fillStyle = color;
  ctx.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h));
}

function room(ctx: CanvasRenderingContext2D, light: Light, frame: number) {
  rect(ctx, COLORS.void, 0, 0, WIDTH, HEIGHT);
  rect(ctx, COLORS.graphite, 0, 0, WIDTH, WALL_BOTTOM);
  // Ventana con el cielo de la hora real.
  const [top, bottom] = SKY[light];
  const sky = ctx.createLinearGradient(0, 20, 0, 100);
  sky.addColorStop(0, top);
  sky.addColorStop(1, bottom);
  ctx.fillStyle = sky;
  ctx.fillRect(240, 20, 160, 80);
  rect(ctx, COLORS.edge, 318, 20, 4, 80);
  rect(ctx, COLORS.edge, 240, 58, 160, 4);
  if (light === "noche") {
    for (let i = 0; i < 7; i += 1) {
      const twinkle = (frame + i * 7) % 40 < 30;
      if (twinkle) rect(ctx, COLORS.snow, 250 + ((i * 37) % 140), 26 + ((i * 23) % 28), 2, 2);
    }
  }
  // Suelo en damero y zócalo.
  for (let y = WALL_BOTTOM; y < HEIGHT; y += 20) {
    for (let x = 0; x < WIDTH; x += 20) {
      rect(ctx, ((x + y) / 20) % 2 === 0 ? COLORS.carbon : "#161616", x, y, 20, 20);
    }
  }
  rect(ctx, COLORS.smoke, 0, WALL_BOTTOM - 6, WIDTH, 6);
  // Zona de descanso: sofá y planta.
  rect(ctx, COLORS.lavender, LOUNGE_X, 250, 100, 26);
  rect(ctx, "#b9b3e6", LOUNGE_X, 236, 100, 16);
  rect(ctx, COLORS.wood, 600, 170, 20, 26);
  rect(ctx, "#3f7a4a", 594, 140, 32, 34);
  // Luz cálida de noche.
  if (light === "noche" || light === "atardecer") {
    ctx.fillStyle = "rgba(255, 186, 106, 0.06)";
    ctx.fillRect(0, WALL_BOTTOM, WIDTH, HEIGHT - WALL_BOTTOM);
  }
}

function desk(ctx: CanvasRenderingContext2D, x: number, y: number, glowing: boolean, frame: number) {
  rect(ctx, COLORS.wood, x, y, 90, 12);
  rect(ctx, "#3d2f22", x + 6, y + 12, 6, 28);
  rect(ctx, "#3d2f22", x + 78, y + 12, 6, 28);
  rect(ctx, COLORS.smoke, x + 30, y - 34, 36, 26); // monitor
  const flicker = glowing && frame % 30 < 28;
  rect(ctx, flicker ? COLORS.cyan : COLORS.edge, x + 33, y - 31, 30, 18);
  if (flicker) {
    for (let line = 0; line < 3; line += 1) {
      const width = 8 + ((frame / 4 + line * 5) % 16);
      rect(ctx, COLORS.void, x + 36, y - 28 + line * 5, width, 2);
    }
  }
  rect(ctx, COLORS.smoke, x + 45, y - 8, 6, 8);
}

function person(ctx: CanvasRenderingContext2D, c: Character, x: number, y: number, pose: Pose, frame: number, index: number) {
  const bob = pose === "esperando" || pose === "pensando" ? (Math.floor(frame / 15) % 2) * PX * 0.5 : 0;
  const top = y - 14 * PX + bob;
  const shirt = SHIRT[c.id] ?? COLORS.lavender;
  rect(ctx, HAIR[index % HAIR.length] ?? COLORS.void, x + PX, top, 4 * PX, 2 * PX);
  rect(ctx, COLORS.skin, x + PX, top + 2 * PX, 4 * PX, 3 * PX);
  rect(ctx, COLORS.void, x + 2 * PX, top + 3 * PX, PX / 2, PX / 2);
  rect(ctx, COLORS.void, x + 4 * PX, top + 3 * PX, PX / 2, PX / 2);
  rect(ctx, shirt, x, top + 5 * PX, 6 * PX, 5 * PX);
  if (pose === "trabajando") {
    const typing = Math.floor(frame / 6) % 2 === 0;
    rect(ctx, COLORS.skin, x - PX, top + (typing ? 7 : 8) * PX, PX, PX);
    rect(ctx, COLORS.skin, x + 6 * PX, top + (typing ? 8 : 7) * PX, PX, PX);
  }
  rect(ctx, COLORS.smoke, x + PX, top + 10 * PX, 2 * PX, 4 * PX);
  rect(ctx, COLORS.smoke, x + 3 * PX, top + 10 * PX, 2 * PX, 4 * PX);
}

function bubble(ctx: CanvasRenderingContext2D, text: string, x: number, y: number, thinking: boolean, frame: number) {
  ctx.font = "11px ui-monospace, Consolas, monospace";
  const shown = thinking ? `${text}${".".repeat(Math.floor(frame / 20) % 4)}` : text;
  const width = Math.min(150, ctx.measureText(text).width + 22);
  const left = Math.max(4, Math.min(WIDTH - width - 4, x - width / 2));
  rect(ctx, COLORS.snow, left, y, width, 18);
  rect(ctx, COLORS.snow, x - 3, y + 18, 6, 4);
  ctx.fillStyle = COLORS.void;
  ctx.fillText(shown, left + 6, y + 13, width - 10);
}

function nameTag(ctx: CanvasRenderingContext2D, name: string, x: number, y: number) {
  ctx.font = "10px ui-monospace, Consolas, monospace";
  ctx.fillStyle = COLORS.ash;
  ctx.fillText(name, x, y);
}

function progressBar(ctx: CanvasRenderingContext2D, done: number, total: number, clock: string) {
  rect(ctx, COLORS.carbon, 16, 108, 200, 10);
  if (total > 0) {
    const fill = ctx.createLinearGradient(16, 0, 216, 0);
    fill.addColorStop(0.09, "#f4c587");
    fill.addColorStop(0.52, "#d6d3e9");
    fill.addColorStop(0.94, "#99d2ec");
    ctx.fillStyle = fill;
    ctx.fillRect(16, 108, Math.round((200 * done) / total), 10);
  }
  ctx.font = "12px ui-monospace, Consolas, monospace";
  ctx.fillStyle = COLORS.snow;
  ctx.fillText(clock, 440, 40);
}

// Dibuja un fotograma y devuelve dónde está cada personaje (para el clic).
export function drawOffice(ctx: CanvasRenderingContext2D, scene: Scene, frame: number, clock: string): HitBox[] {
  ctx.imageSmoothingEnabled = false;
  room(ctx, scene.light, frame);
  progressBar(ctx, scene.progress.done, scene.progress.total, clock);
  const hits: HitBox[] = [];
  let resting = 0;
  scene.characters.forEach((c, index) => {
    const dx = deskX(c.desk);
    const dy = deskY(c.desk);
    desk(ctx, dx, dy, c.pose === "trabajando", frame + index * 9);
    const onSofa = c.pose === "descansando";
    const x = onSofa ? LOUNGE_X + 8 + resting * 30 : dx + 30;
    const y = onSofa ? 250 : dy + (c.pose === "trabajando" ? 10 : 30);
    if (onSofa) resting += 1;
    person(ctx, c, x, y, c.pose, frame, index);
    nameTag(ctx, c.name, x - 2, y + 14);
    bubble(ctx, c.bubble, x + 12, y - 14 * PX - 30, c.pose === "pensando", frame);
    hits.push({ id: c.id, x: x - 10, y: y - 14 * PX - 30, w: 50, h: 14 * PX + 50 });
  });
  return hits;
}

export function hitAt(hits: HitBox[], x: number, y: number): string | null {
  const found = hits.find((h) => x >= h.x && x <= h.x + h.w && y >= h.y && y <= h.y + h.h);
  return found?.id ?? null;
}
