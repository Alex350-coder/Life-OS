import { useEffect, useMemo, useRef, useState, type MouseEvent, type RefObject } from "react";
import { get } from "../api/client";
import type { Agents, Briefing, Indicators } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorBanner } from "../components/common";
import { drawOffice, hitAt, HEIGHT, WIDTH, type HitBox } from "../office/draw";
import { frameInterval, prefersReducedMotion } from "../office/motion";
import { STILL_FRAME } from "../office/pause";
import { detectReactions, REACTION_MS, type Reaction } from "../office/reactions";
import { buildScene } from "../office/scene";
import { SEASON_LABEL } from "../office/season";
import { MyDayPanel } from "./MyDayPanel";

const REFRESH_MS = 60_000; // los datos del día se recargan cada minuto (solo si se ve)
const STREAK_DAYS = 90; // ventana de /api/indicadores para la racha (se pide una vez)

interface Timed extends Reaction {
  at: number;
}

function clockText(now: Date): string {
  return now.toLocaleTimeString("es", { hour: "2-digit", minute: "2-digit" });
}

// La última lectura válida de /api/hoy: al recargar cada minuto la escena no parpadea y se puede
// comparar con la anterior para reaccionar a lo que cambió. Se ajusta el estado durante el render
// (patrón de React para "información del render anterior"), sin un efecto extra.
function useLatestDay(data: Briefing | null, now: Date): { day: Briefing | null; reactions: Timed[] } {
  const [day, setDay] = useState<Briefing | null>(null);
  const [reactions, setReactions] = useState<Timed[]>([]);
  if (data !== null && data !== day) {
    const at = now.getTime(); // hora de la recarga que trajo estos datos
    const found = detectReactions(day, data, now);
    setDay(data);
    if (found.length > 0) {
      const recent = reactions.filter((r) => at - r.at < REACTION_MS);
      setReactions([...recent, ...found.map((r) => ({ ...r, at }))]);
    }
  }
  return { day, reactions };
}

// Las reacciones duran REACTION_MS: un temporizador marca cuándo caducaron (en el canvas, en el
// fotograma fijo y en el texto anunciado), sin leer la hora durante el render.
function useActiveReactions(reactions: Timed[]): Timed[] {
  const [expiredAt, setExpiredAt] = useState(0);
  useEffect(() => {
    const last = reactions.at(-1);
    if (last === undefined) return;
    const timer = window.setTimeout(() => setExpiredAt(last.at + REACTION_MS), REACTION_MS);
    return () => window.clearTimeout(timer);
  }, [reactions]);
  return useMemo(() => reactions.filter((r) => r.at + REACTION_MS > expiredAt), [reactions, expiredAt]);
}

// ¿Se ve el canvas? Las pestañas visitadas siguen montadas pero ocultas: sin observador
// (jsdom) se supone visible.
function useVisible(target: RefObject<HTMLElement | null>): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const element = target.current;
    if (element === null || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver((entries) => {
      setVisible(entries.some((entry) => entry.isIntersecting));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [target]);
  return visible;
}

// Oficina visual (docs/specs/interfaz-grafica.md, ADR-0019): la misma información que Hoy y
// Agentes, como una escena. No escribe nada; un clic en un personaje abre su vista. La lista de
// abajo dice lo mismo en texto para lectores de pantalla y teclado.
export function OfficeView() {
  const [tick, setTick] = useState(0);
  const today = useApi(() => get<Briefing>("/api/hoy"), `oficina-hoy-${tick}`);
  const agents = useApi(() => get<Agents>("/api/agentes"), "oficina-agentes");
  const indicators = useApi(() => get<Indicators>("/api/indicadores", { dias: STREAK_DAYS }), "oficina-racha");
  const canvas = useRef<HTMLCanvasElement>(null);
  const hits = useRef<HitBox[]>([]);
  const frame = useRef(0); // sobrevive a las recargas: la animación no salta cada minuto
  const [now, setNow] = useState(() => new Date());
  const { day, reactions } = useLatestDay(today.data, now);
  const shown = useActiveReactions(reactions);
  const visible = useVisible(canvas);

  // Recarga cada minuto mientras se ve; al volver a verse, recarga enseguida.
  useEffect(() => {
    if (!visible) return;
    const refresh = () => {
      setTick((n) => n + 1);
      setNow(new Date());
    };
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [visible]);
  const [seen, setSeen] = useState(visible);
  if (visible !== seen) {
    setSeen(visible);
    if (visible) setTick((n) => n + 1);
  }

  const scene = useMemo(() => buildScene(day, agents.data?.agents ?? [], now), [day, agents.data, now]);
  const clock = clockText(now);

  useEffect(() => {
    const ctx = canvas.current?.getContext("2d") ?? null;
    if (ctx === null || !visible) return; // sin canvas (jsdom) queda la lista; oculta, no se dibuja
    if (prefersReducedMotion()) {
      // Un fotograma fijo; las reacciones también se anuncian en el texto de abajo.
      hits.current = drawOffice(ctx, scene, STILL_FRAME, clock, shown);
      return;
    }
    const interval = frameInterval(navigator as Navigator & { deviceMemory?: number });
    let last = 0;
    let handle = 0;
    const loop = (time: number) => {
      if (!document.hidden && time - last >= interval) {
        last = time;
        frame.current += 1;
        hits.current = drawOffice(ctx, scene, frame.current, clock, shown);
      }
      handle = window.requestAnimationFrame(loop);
    };
    handle = window.requestAnimationFrame(loop);
    return () => window.cancelAnimationFrame(handle);
  }, [scene, clock, shown, visible]);

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

  const latest = shown.at(-1);
  return (
    <div className="view">
      <ErrorBanner error={today.error ?? agents.error} />
      <section aria-labelledby="office-heading" className="card office">
        <div className="office-head">
          <h2 id="office-heading">Oficina</h2>
          <p className="muted small">
            {scene.headline} · {SEASON_LABEL[scene.season]}
          </p>
          <button type="button" className="ghost" onClick={() => (window.location.hash = "hoy")}>
            Vista sencilla
          </button>
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
        <p role="status" className="office-event">
          {latest === undefined ? "" : `${clockText(new Date(latest.at))} · ${latest.text}`}
        </p>
        <MyDayPanel day={day} indicators={indicators.data} now={now} />
        {scene.pause !== null && (
          <p className="muted small">
            Tiempo libre: {scene.pause.lines.map((line) => line.text).join(" · ")}
          </p>
        )}
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
