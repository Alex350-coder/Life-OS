import { useState } from "react";
import { get, post } from "../../api/client";
import type { ProposalItem, ReviewItem } from "../../api/types";
import { useApi } from "../../api/useApi";
import { ErrorBanner, Loading } from "../../components/common";

export function ProposalItemText({ item }: { item: ProposalItem }) {
  if (item.kind === "prioridad") {
    return (
      <>
        Prioridad de <code className="task-id">{item.task_id}</code> → <strong>{item.priority}</strong> ·{" "}
        <span className="muted">{item.reason}</span>
      </>
    );
  }
  return (
    <>
      Tarea nueva: <strong>{item.title}</strong> ({item.effort}
      {item.due !== null && `, vence ${item.due}`}, {item.priority}) · <span className="muted">{item.reason}</span>
    </>
  );
}

function Pending({ review }: { review: ReviewItem }) {
  if (review.kind !== "prioridad") return <ProposalItemText item={review} />;
  return (
    <>
      Prioridad de <strong>{review.task_title ?? review.task_id}</strong>: {review.current_priority ?? "?"} →{" "}
      <strong>{review.priority}</strong> · <span className="muted">{review.reason}</span>
    </>
  );
}

// Solo el propietario acepta o rechaza (RULES C.3, C.5): aceptar aplica el cambio en la bóveda.
export function ProposalsPanel({ version }: { version: number }) {
  const pending = useApi(() => get<{ items: ReviewItem[] }>("/api/propuestas"), `propuestas-${version}`);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function decide(ref: string, accept: boolean) {
    setBusy(ref);
    setError(null);
    setNotice(null);
    try {
      await post(accept ? "/api/propuestas/aceptacion" : "/api/propuestas/rechazo", { ref });
      setNotice(`${accept ? "Aceptada" : "Rechazada"} ${ref}.`);
      pending.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  const items = pending.data?.items ?? [];
  return (
    <section className="card" aria-labelledby="proposals-heading">
      <h3 id="proposals-heading">Propuestas de los especialistas</h3>
      <p className="muted small">No cambian nada hasta que las aceptes. El Jefe planifica con lo aceptado.</p>
      <ErrorBanner error={pending.error} />
      {notice !== null && (
        <p role="status" className="banner banner-ok">
          {notice}
        </p>
      )}
      {error !== null && (
        <p role="alert" className="banner banner-error">
          {error}
        </p>
      )}
      <Loading loading={pending.loading}>
        {items.length === 0 ? (
          <p className="muted">No hay propuestas pendientes.</p>
        ) : (
          <ul className="blocks">
            {items.map((review) => (
              <li key={review.ref}>
                <code className="task-id">{review.ref}</code> <Pending review={review} />{" "}
                <span className="row">
                  <button type="button" disabled={busy !== null} onClick={() => void decide(review.ref, true)}>
                    Aceptar
                  </button>
                  <button
                    type="button"
                    className="ghost"
                    disabled={busy !== null}
                    onClick={() => void decide(review.ref, false)}
                  >
                    Rechazar
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </Loading>
    </section>
  );
}
