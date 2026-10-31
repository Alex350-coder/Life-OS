import { useState } from "react";
import { get } from "../api/client";
import type { Week } from "../api/types";
import { useApi } from "../api/useApi";
import { BlockList, ErrorBanner, Loading, LoadBar } from "../components/common";
import { JefeChat } from "./JefeChat";

const WEEK_PATTERN = /^\d{4}-W\d{2}$/;
const DAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"];

function Plan({ week }: { week: Week }) {
  const status =
    week.status === null
      ? "sin plan"
      : `${week.status}${week.revision > 0 ? ` (revisión ${week.revision})` : ""}`;
  return (
    <section aria-labelledby="week-heading" className="card">
      <h2 id="week-heading">
        Semana {week.week} · {status}
      </h2>
      {week.path !== null && <p className="muted">Nota: {week.path}</p>}
      {week.justification !== "" && week.justification !== "—" && <p>{week.justification}</p>}
      <div className="days">
        {week.days.map((day, index) => {
          const load = week.load[index];
          const blocks = week.blocks.filter((block) => block.day === day);
          return (
            <article key={day} className="day" aria-label={`${DAY_NAMES[index] ?? ""} ${day}`}>
              <h3>
                {DAY_NAMES[index]} <span className="muted">{day}</span>
              </h3>
              {load !== undefined && <LoadBar load={load} label={DAY_NAMES[index] ?? day} />}
              <BlockList blocks={blocks} empty="Sin bloques." />
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function WeekView() {
  const [week, setWeek] = useState("");
  const [version, setVersion] = useState(0);
  const isValid = week === "" || WEEK_PATTERN.test(week);
  const current = useApi(
    () => get<Week>("/api/semana", isValid ? { semana: week } : {}),
    `${isValid ? week : ""}-${version}`,
  );

  return (
    <div className="view">
      <label className="inline">
        Semana (AAAA-Wnn, vacío = la actual)
        <input
          value={week}
          maxLength={8}
          placeholder="2026-W45"
          aria-invalid={!isValid}
          onChange={(e) => setWeek(e.target.value.trim())}
        />
      </label>
      <ErrorBanner error={current.error} />
      <JefeChat week={isValid ? week : ""} onChanged={() => setVersion((n) => n + 1)} />
      <Loading loading={current.loading && current.data === null}>
        {current.data !== null && <Plan week={current.data} />}
      </Loading>
    </div>
  );
}
