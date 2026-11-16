import type { Briefing, Indicators } from "../api/types";
import { minutes } from "../components/common";
import { myDay } from "../office/myDay";

type Props = {
  day: Briefing | null;
  indicators: Indicators | null;
  now: Date;
};

// Panel «Mi día» de la Oficina (F9-T04): progreso como en un juego, sin castigar los días flojos.
export function MyDayPanel({ day, indicators, now }: Props) {
  const summary = myDay(day, indicators, now);
  const focus = summary.focusMaxMinutes === null ? "límite desconocido" : `límite ${minutes(summary.focusMaxMinutes)}`;
  const streak = summary.streak === null ? "—" : `${summary.streak} día(s)`;
  return (
    <section aria-labelledby="my-day-heading" className="my-day">
      <h3 id="my-day-heading">Mi día</h3>
      <label className="my-day-progress">
        Trabajo hecho: {minutes(summary.doneMinutes)} de {minutes(summary.workMinutes)}
        <progress max={Math.max(summary.workMinutes, 1)} value={summary.doneMinutes} />
      </label>
      <dl className="stats">
        <div className="stat">
          <dt>Foco planificado</dt>
          <dd>
            <strong>{minutes(summary.workMinutes)}</strong> <span className="muted">{focus}</span>
          </dd>
        </div>
        <div className="stat">
          <dt>Racha de cierres</dt>
          <dd>
            <strong>{streak}</strong>
          </dd>
        </div>
        <div className="stat">
          <dt>Check-ins hoy</dt>
          <dd>
            <strong>{summary.checkins ?? "—"}</strong>
          </dd>
        </div>
        <div className="stat">
          <dt>Siguiente</dt>
          <dd>
            <strong>{summary.next ?? "nada más hoy"}</strong>
          </dd>
        </div>
      </dl>
      {summary.overLimit && (
        <p className="banner banner-warn">El trabajo planificado hoy supera tu límite de foco diario.</p>
      )}
      <p className="muted">{summary.cheer}</p>
    </section>
  );
}
