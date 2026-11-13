// Decoración por estación y destellos de las reacciones (F9-T04). Canvas 2D propio (ADR-0019):
// rectángulos de color, sin imágenes ni dependencias. Nada aquí representa un dato.
import type { Season } from "./season";

const WINDOW = { x: 240, y: 20, w: 160, h: 80 };
const PLANT = { x: 594, y: 140, w: 32, h: 34 };

function rect(ctx: CanvasRenderingContext2D, color: string, x: number, y: number, w: number, h: number) {
  ctx.fillStyle = color;
  ctx.fillRect(Math.round(x), Math.round(y), Math.round(w), Math.round(h));
}

const LEAF: Record<Season, string> = {
  verano: "#4f9a5a",
  otono: "#c9803a",
  invierno: "#3b6a4a",
  primavera: "#3f7a4a",
};

function summer(ctx: CanvasRenderingContext2D, frame: number) {
  const glow = frame % 48 < 24 ? 0 : 1;
  rect(ctx, "#ffd27a", WINDOW.x + 120 - glow, WINDOW.y + 8 - glow, 14 + glow * 2, 14 + glow * 2);
}

function autumn(ctx: CanvasRenderingContext2D, frame: number) {
  for (let i = 0; i < 4; i += 1) {
    const fall = (frame * 1.5 + i * 23) % WINDOW.h;
    const sway = Math.sin((frame + i * 10) / 6) * 4;
    rect(ctx, i % 2 === 0 ? "#c9803a" : "#e0a35a", WINDOW.x + 20 + i * 36 + sway, WINDOW.y + fall, 4, 3);
  }
}

function winter(ctx: CanvasRenderingContext2D, frame: number) {
  // Garúa de Lima: líneas finas y un cielo algo más gris.
  ctx.fillStyle = "rgba(160, 170, 185, 0.25)";
  ctx.fillRect(WINDOW.x, WINDOW.y, WINDOW.w, WINDOW.h);
  for (let i = 0; i < 10; i += 1) {
    const drop = (frame * 3 + i * 17) % WINDOW.h;
    rect(ctx, "rgba(220, 230, 240, 0.7)", WINDOW.x + 6 + i * 15, WINDOW.y + drop, 1, 5);
  }
}

function spring(ctx: CanvasRenderingContext2D, frame: number) {
  const colors = ["#f4a6c1", "#ffd27a", "#d8d3ff"];
  for (let i = 0; i < 5; i += 1) {
    const open = (frame + i * 9) % 60 < 50 ? 4 : 3;
    rect(ctx, colors[i % colors.length] ?? "#f4a6c1", PLANT.x + 2 + i * 6, PLANT.y + 2 + (i % 2) * 8, open, open);
  }
}

export function drawSeason(ctx: CanvasRenderingContext2D, season: Season, frame: number) {
  rect(ctx, LEAF[season], PLANT.x, PLANT.y, PLANT.w, PLANT.h);
  if (season === "verano") summer(ctx, frame);
  else if (season === "otono") autumn(ctx, frame);
  else if (season === "invierno") winter(ctx, frame);
  else spring(ctx, frame);
}

// Destellos alrededor de un personaje que celebra algo (dura REACTION_MS).
export function sparkle(ctx: CanvasRenderingContext2D, x: number, y: number, frame: number) {
  const colors = ["#f4c587", "#d6d3e9", "#99d2ec"];
  for (let i = 0; i < 6; i += 1) {
    const angle = (i / 6) * Math.PI * 2 + frame / 8;
    const radius = 18 + (frame % 12);
    rect(ctx, colors[i % colors.length] ?? "#ffffff", x + Math.cos(angle) * radius, y + Math.sin(angle) * radius, 3, 3);
  }
}
