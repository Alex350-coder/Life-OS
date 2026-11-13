import { useState } from "react";
import { get } from "../api/client";
import type { Indicators } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorBanner, Loading } from "../components/common";

// Ventanas fijas: el backend acepta de 1 a 90 días (F9-T03).
const WINDOWS = [7, 28, 90] as const;
type WindowDays = (typeof WINDOWS)[number];

function pct(value: number | null): string {
  return value === null ? "desconocido" : `${value} %`;
}

interface StatProps {
  label: string;
  value: string;
  detail: string;
}

function Stat({ label, value, detail }: StatProps) {
  return (
    <div className="stat">
      <dt>{label}</dt>
      <dd>
        <strong>{value}</strong>
        <span className="muted"> {detail}</span>
      </dd>
    </div>
  );
}

function Summary({ data }: { data: Indicators }) {
  const { deliveries: d, adherence: a, usage: u, acceptance: p } = data;
  return (
    <dl className="stats">
      <Stat label="Entregas cumplidas" value={pct(d.percent)} detail={`${d.completed} de ${d.due}`} />
      <Stat label="Tareas olvidadas" value={String(d.forgotten)} detail="abiertas con la fecha límite pasada" />
      <Stat label="Cumplimiento del plan" value={pct(a.percent)} detail={`en ${a.closed_days} cierres`} />
      <Stat label="Uso" value={`${u.days_per_week} días/semana`} detail={`${u.active_days} días activos`} />
      <Stat
        label="Planes con cambios menores"
        value={pct(p.percent)}
        detail={`${p.minor_changes} de ${p.approved} aprobados`}
      />
    </dl>
  );
}

function Notifications({ data }: { data: Indicators["notifications"] }) {
  const kinds = data.by_kind.map((item) => `${item.kind} ${item.count}`).join(", ") || "ninguno";
  return (
    <section aria-labelledby="notifications-heading" className="card">
      <h3 id="notifications-heading">Carga de notificaciones</h3>
      <p>
        {data.sent} enviadas, {data.failed} fallidas y {data.skipped} omitidas. Máximo en un día:{" "}
        {data.max_per_day}; media por día con avisos:{" "}
        {data.per_active_day === null ? "desconocida" : data.per_active_day}.
      </p>
      <p className="muted">Por tipo: {kinds}</p>
    </section>
  );
}

function Costs({ rows }: { rows: Indicators["costs"] }) {
  if (rows.length === 0) return <p className="muted">Sin llamadas a modelos en este periodo.</p>;
  return (
    <div className="table-wrap">
      <table>
        <caption>Costo y latencia por agente y ritual</caption>
        <thead>
          <tr>
            <th scope="col">Agente / ritual</th>
            <th scope="col">Llamadas</th>
            <th scope="col">Con error</th>
            <th scope="col">Mediana</th>
            <th scope="col">p95</th>
            <th scope="col">Costo estimado</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.agent}/${row.ritual}`}>
              <td>
                {row.agent} / {row.ritual}
              </td>
              <td>{row.calls}</td>
              <td>{row.errors}</td>
              <td>{row.latency_median_ms} ms</td>
              <td>{row.latency_p95_ms} ms</td>
              <td>
                {row.cost_estimated_usd} USD
                {row.cost_unknown > 0 && <span className="muted"> · {row.cost_unknown} sin costo conocido</span>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function QualityWarning({ quality }: { quality: Indicators["quality"] }) {
  const found = [
    [quality.malformed_log_lines, "líneas de log ilegibles"],
    [quality.unreadable_notes, "notas que no se pudieron leer"],
    [quality.invalid_task_lines, "líneas de tareas con formato no válido"],
    [quality.unreadable_closings, "cierres sin respuestas reconocibles"],
  ] as const;
  const text = found.filter(([count]) => count > 0).map(([count, what]) => `${count} ${what}`);
  if (text.length === 0) return null;
  return <p className="banner banner-warn">No cuentan: {text.join("; ")}.</p>;
}

export function IndicatorsView() {
  const [days, setDays] = useState<WindowDays>(28);
  const report = useApi(() => get<Indicators>("/api/indicadores", { dias: days }), `indicadores-${days}`);
  const data = report.data;
  return (
    <div className="view">
      <section aria-labelledby="indicators-heading" className="card">
        <h2 id="indicators-heading">Indicadores</h2>
        <p className="muted">
          Utilidad real del sistema (PLAN §13), calculada con tus notas y los registros. Solo lectura.
        </p>
        <label>
          Periodo{" "}
          <select value={days} onChange={(event) => setDays(Number(event.target.value) as WindowDays)}>
            {WINDOWS.map((value) => (
              <option key={value} value={value}>
                últimos {value} días
              </option>
            ))}
          </select>
        </label>
        <ErrorBanner error={report.error} />
        <Loading loading={report.loading}>
          {data !== null && (
            <>
              <p className="muted">
                Del {data.start} al {data.end}
              </p>
              <Summary data={data} />
              <Notifications data={data.notifications} />
              <Costs rows={data.costs} />
              <h3>Sin medición automática</h3>
              <ul>
                {data.not_measured.map((item) => (
                  <li key={item.name}>
                    <strong>{item.name}:</strong> {item.reason}
                  </li>
                ))}
              </ul>
              <QualityWarning quality={data.quality} />
            </>
          )}
        </Loading>
      </section>
    </div>
  );
}
