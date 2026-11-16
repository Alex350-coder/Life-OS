import { useState } from "react";
import { get } from "../api/client";
import type { CalendarAvailability, CalendarGrant, CalendarStatus } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorBanner, Loading } from "../components/common";

// Solo lectura (F10-T04, DT-27): conectar, publicar y revocar exigen la CLI con su vista
// previa y --confirmar (ADR-0017). Esta vista no tiene ningún botón que escriba en Google.
const WEEK_PATTERN = /^\d{4}-W\d{2}$/;
const GRANT_LABELS: Record<CalendarGrant["grant"], string> = {
  lectura: "Lectura (disponibilidad)",
  escritura: "Escritura (calendario propio Life OS)",
};

function GrantRow({ item }: { item: CalendarGrant }) {
  const state = item.connected
    ? `conectado${item.obtained !== null ? ` desde ${item.obtained.slice(0, 10)}` : ""}`
    : "sin conectar";
  return (
    <li>
      <strong>{GRANT_LABELS[item.grant]}:</strong> {state}
      {item.problem !== null && <p className="banner banner-warn">Problema: {item.problem}</p>}
    </li>
  );
}

function Status({ status }: { status: CalendarStatus }) {
  if (!status.configured) {
    return (
      <p className="muted">
        Google Calendar no está configurado. Sigue los pasos del README y conecta con{" "}
        <code>uv run lifeos-calendario conectar --lectura</code>.
      </p>
    );
  }
  const readConnected = status.grants.some((g) => g.grant === "lectura" && g.connected);
  return (
    <>
      {status.problem !== null && (
        <p className="banner banner-warn">Problema de configuración: {status.problem}</p>
      )}
      <ul>
        {status.grants.map((item) => (
          <GrantRow key={item.grant} item={item} />
        ))}
      </ul>
      {readConnected && !status.can_read && (
        <p className="banner banner-warn">
          La lectura se conectó después de arrancar: reinicia lifeos-web para ver la disponibilidad.
        </p>
      )}
      <p className="muted small">
        Para conectar, publicar o revocar usa <code>lifeos-calendario</code> en la terminal.
      </p>
    </>
  );
}

function Availability({ data }: { data: CalendarAvailability }) {
  return (
    <section aria-labelledby="availability-heading" className="card">
      <h3 id="availability-heading">
        Disponibilidad de {data.week} <span className="muted">({data.zone})</span>
      </h3>
      {data.busy.length === 0 ? (
        <p>Sin intervalos ocupados.</p>
      ) : (
        <ul>
          {data.busy.map((item) => (
            <li key={`${item.source}|${item.start}|${item.end}`}>
              {item.day} {item.span} · {item.source} · confianza {item.confidence}
            </li>
          ))}
        </ul>
      )}
      {data.plan_path === null ? (
        <p className="muted">Sin plan para esta semana.</p>
      ) : (
        <p className="muted">
          Plan vigente: {data.plan_path} ({data.plan_status ?? "?"})
        </p>
      )}
      {data.plan_path !== null && data.clashes.length === 0 && (
        <p>Ningún bloque de trabajo choca con el calendario.</p>
      )}
      {data.clashes.length > 0 && (
        <div role="alert" className="banner banner-warn">
          <p>
            {data.clashes.length === 1 ? "1 choque" : `${data.clashes.length} choques`} con el
            calendario. Replanifica la semana.
          </p>
          <ul>
            {data.clashes.map(({ block, busy }) => (
              <li key={`${block.start}|${busy.source}|${busy.start}`}>
                Choque: «{block.title}» {block.day} {block.span} con {busy.source} (confianza{" "}
                {busy.confidence})
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}

function AvailabilityLoader({ week }: { week: string }) {
  const current = useApi(
    () => get<CalendarAvailability>("/api/calendario/disponibilidad", { semana: week }),
    `calendario-${week}`,
  );
  return (
    <>
      <ErrorBanner error={current.error} />
      <Loading loading={current.loading && current.data === null}>
        {current.data !== null && <Availability data={current.data} />}
      </Loading>
    </>
  );
}

export function CalendarView() {
  const [week, setWeek] = useState("");
  const isValid = week === "" || WEEK_PATTERN.test(week);
  const status = useApi(() => get<CalendarStatus>("/api/calendario"), "calendario-estado");
  return (
    <div className="view">
      <section aria-labelledby="calendar-heading" className="card">
        <h2 id="calendar-heading">Google Calendar</h2>
        <p className="muted">Solo lectura: permisos conectados y lo ocupado de la semana.</p>
        <ErrorBanner error={status.error} />
        <Loading loading={status.loading && status.data === null}>
          {status.data !== null && <Status status={status.data} />}
        </Loading>
      </section>
      {status.data?.can_read === true && (
        <>
          <label className="inline">
            Semana (AAAA-Wnn, vacío = la actual)
            <input
              value={week}
              maxLength={8}
              placeholder="2026-W45"
              aria-invalid={!isValid}
              aria-describedby="calendar-week-format"
              onChange={(e) => setWeek(e.target.value.trim())}
            />
          </label>
          <p id="calendar-week-format" className={isValid ? "muted small" : "banner banner-warn"}>
            Formato AAAA-Wnn, p. ej., 2026-W45.
            {!isValid && " Corrígelo para ver la disponibilidad de esa semana."}
          </p>
          {isValid && <AvailabilityLoader week={week} />}
        </>
      )}
    </div>
  );
}
