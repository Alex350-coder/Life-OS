import { get } from "../api/client";
import type { Briefing } from "../api/types";
import { useApi } from "../api/useApi";
import { BlockItem, BlockList, ErrorBanner, Loading } from "../components/common";
import { ClosingForm } from "./ClosingForm";
import { CoachChat } from "./CoachChat";

function Now({ day }: { day: Briefing }) {
  return (
    <section aria-labelledby="now-heading" className="card">
      <h2 id="now-heading">
        Hoy {day.day} · {day.approved ? "plan aprobado" : "plan sin aprobar"}
      </h2>
      {!day.readable && (
        <p className="banner banner-warn">
          La nota de hoy no sigue el formato del plan; revisa sus bloques.
        </p>
      )}
      {day.resting !== null && (
        <p className="banner banner-rest">
          Tiempo protegido ahora: {day.resting.span} {day.resting.title}. Descansa sin culpa.
        </p>
      )}
      <dl className="now">
        <dt>Ahora</dt>
        <dd>{day.current === null ? "Sin bloque" : <ul className="blocks"><BlockItem block={day.current} /></ul>}</dd>
        <dt>Siguiente</dt>
        <dd>{day.upcoming === null ? "Nada más hoy" : <ul className="blocks"><BlockItem block={day.upcoming} /></ul>}</dd>
      </dl>
      <h3>Bloques de trabajo</h3>
      <BlockList blocks={day.work} empty="Sin bloques de trabajo hoy." />
      <h3>Tiempo protegido</h3>
      <BlockList blocks={day.protected} empty="Sin tiempo protegido hoy." />
      <p className="muted">
        Check-ins: {day.checkins}
        {day.adjustments.length > 0 &&
          ` · Propuestas para el Jefe: ${day.adjustments.length} (revísalas en Semana → Replanificar)`}
      </p>
      {day.rejected_adjustments > 0 && (
        <p className="banner banner-warn">
          {day.rejected_adjustments} línea(s) «ajuste:» no siguen el formato y el Jefe no las verá.
        </p>
      )}
    </section>
  );
}

export function TodayView() {
  const today = useApi(() => get<Briefing>("/api/hoy"), "hoy");
  const refresh = today.reload;

  return (
    <div className="view">
      <ErrorBanner error={today.error} />
      <Loading loading={today.loading && today.data === null}>
        {today.data !== null && <Now day={today.data} />}
      </Loading>
      <CoachChat work={today.data?.work ?? []} onRecorded={refresh} />
      {today.data !== null && <ClosingForm key={today.data.day} day={today.data} onApplied={refresh} />}
    </div>
  );
}
