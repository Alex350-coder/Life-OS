import { useEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { get } from "../api/client";
import type { Agents, Briefing } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorBanner, minutes } from "../components/common";
import { drawOffice, hitAt, HEIGHT, WIDTH, type HitBox } from "../office/draw";
import { buildScene } from "../office/scene";

const REFRESH_MS = 60_000; // los datos del día se recargan cada minuto
const FRAME_MS = 1000 / 12; // 12 fotogramas por segundo bastan para el pixel art

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function clockText(now: Date): string {
  return now.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
}

// Oficina visual (docs/specs/interfaz-grafica.md): la misma información que Hoy y Agentes, como
// una escena. No escribe nada; un clic en un personaje abre su vista. La lista de abajo dice lo
// mismo en texto para lectores de pantalla y teclado.
export function OfficeView() {
  const [tick, setTick] = useState(0);
  const today = useApi(() => get<Briefing>("/api/hoy"), `oficina-hoy-${tick}`);
  const agents = useApi(() => get<Agents>("/api/agentes"), "oficina-agentes");
  const canvas = useRef<HTMLCanvasElement>(null);
  const hits = useRef<HitBox[]>([]);
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const refresh = window.setInterval(() => {
      setTick((n) => n + 1);
      setNow(new Date());
    }, REFRESH_MS);
    return () => window.clearInterval(refresh);
  }, []);

  const scene = useMemo(
    () => buildScene(today.data, agents.data?.agents ?? [], now),
    [today.data, agents.data, now],
  );

  useEffect(() => {
    const ctx = canvas.current?.getContext("2d") ?? null;
    if (ctx === null) return; // sin canvas (jsdom o navegador sin soporte): queda la lista
    const clock = clockText(now);
    if (prefersReducedMotion()) {
      hits.current = drawOffice(ctx, scene, 0, clock);
      return;
    }
    let frame = 0;
    let last = 0;
    let handle = 0;
    const loop = (time: number) => {
      if (time - last >= FRAME_MS) {
        last = time;
        frame += 1;
        hits.current = drawOffice(ctx, scene, frame, clock);
      }
      handle = window.requestAnimationFrame(loop);
    };
    handle = window.requestAnimationFrame(loop);
    return () => window.cancelAnimationFrame(handle);
  }, [scene, now]);

  function open(id: string) {
    const character = scene.characters.find((c) => c.id === id);
    if (character !== undefined) window.location.hash = character.opens;
  }

  function handleClick(event: MouseEvent<HTMLCanvasElement>) {
    const box = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - box.left) / box.width) * WIDTH;
    const y = ((event.clientY - box.top) / box.height) * HEIGHT;
    const id = hitAt(hits.current, x, y);
    if (id !== null) open(id);
  }

  return (
    <div className="view">
      <ErrorBanner error={today.error ?? agents.error} />
      <section aria-labelledby="office-heading" className="card office">
        <div className="office-head">
          <h2 id="office-heading">Oficina</h2>
          <p className="muted small">{scene.headline}</p>
        </div>
        {/* Decorativo para la tecnología de apoyo: la lista de abajo dice lo mismo y sus botones
            hacen lo mismo que el clic (teclado y lector de pantalla). */}
        <canvas
          ref={canvas}
          className="office-canvas"
          width={WIDTH}
          height={HEIGHT}
          aria-hidden="true"
          onClick={handleClick}
        />
        <p className="muted small">
          Trabajo del día: {minutes(scene.progress.done)} de {minutes(scene.progress.total)}
        </p>
        <ul className="office-list" aria-label="Quién está en la oficina">
          {scene.characters.map((c) => (
            <li key={c.id} className={`office-person pose-${c.pose}`}>
              <button type="button" className="ghost" onClick={() => open(c.id)}>
                <span className="office-name">{c.name}</span>
                <span className="office-role">{c.role}</span>
              </button>
              <span>{c.status}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
