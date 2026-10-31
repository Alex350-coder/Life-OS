import { get } from "../api/client";
import type { History, HistoryEntry } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorBanner, Loading } from "../components/common";

const RESULT_LABEL: Record<HistoryEntry["result"], string> = {
  ok: "hecho",
  rechazado: "rechazado",
  error: "error",
  intento: "sin resultado",
};

function when(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleString("es");
}

export function HistoryView() {
  const history = useApi(() => get<History>("/api/historial", { limite: 200 }), "historial");
  const data = history.data;
  return (
    <div className="view">
      <section aria-labelledby="history-heading" className="card">
        <h2 id="history-heading">Historial de cambios</h2>
        <p className="muted">Escrituras y rechazos registrados en la auditoría (este mes y el anterior).</p>
        <ErrorBanner error={history.error} />
        <Loading loading={history.loading}>
          {data !== null && data.entries.length === 0 && <p className="muted">Sin cambios registrados.</p>}
          {data !== null && data.entries.length > 0 && (
            <div className="table-wrap">
              <table>
                <caption>De lo más reciente a lo más antiguo</caption>
                <thead>
                  <tr>
                    <th scope="col">Cuándo</th>
                    <th scope="col">Quién</th>
                    <th scope="col">Acción</th>
                    <th scope="col">Destino</th>
                    <th scope="col">Resultado</th>
                  </tr>
                </thead>
                <tbody>
                  {data.entries.map((entry, index) => (
                    <tr key={`${entry.ts}-${entry.action}-${entry.target}-${index}`} className={entry.completed ? "" : "row-warn"}>
                      <td>{when(entry.ts)}</td>
                      <td>{entry.actor}</td>
                      <td>{entry.action}</td>
                      <td>{entry.target}</td>
                      <td>
                        {!entry.completed && <strong>⚠ incompleto: </strong>}
                        {RESULT_LABEL[entry.result]}
                        {entry.reason !== null && <span className="muted"> · {entry.reason}</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {data !== null && data.truncated && <p className="muted">Se muestran los 200 más recientes.</p>}
          {data !== null && data.malformed > 0 && (
            <p className="banner banner-warn">{data.malformed} línea(s) de la auditoría no se pudieron leer.</p>
          )}
        </Loading>
      </section>
    </div>
  );
}
