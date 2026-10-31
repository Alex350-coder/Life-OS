import type { ReactNode } from "react";
import type { DayLoad, PlanBlock } from "../api/types";

const KIND_LABEL: Record<PlanBlock["kind"], string> = {
  trabajo: "Trabajo",
  fijo: "Fijo",
  protegido: "Protegido",
};

export function minutes(total: number): string {
  const hours = Math.floor(total / 60);
  const rest = total % 60;
  if (hours === 0) return `${rest} min`;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

export function ErrorBanner({ error }: { error: Error | null }) {
  if (error === null) return null;
  return (
    <p role="alert" className="banner banner-error">
      {error.message}
    </p>
  );
}

export function Loading({ loading, children }: { loading: boolean; children: ReactNode }) {
  if (loading) return <p aria-live="polite">Cargando…</p>;
  return <>{children}</>;
}

export function BlockItem({ block }: { block: PlanBlock }) {
  return (
    <li className={`block block-${block.kind}`}>
      <span className="block-span">{block.span}</span>
      <span className="block-title">{block.title}</span>
      <span className="tag">{KIND_LABEL[block.kind]}</span>
      {block.task_id !== null && <code className="task-id">{block.task_id}</code>}
    </li>
  );
}

export function BlockList({ blocks, empty }: { blocks: PlanBlock[]; empty: string }) {
  if (blocks.length === 0) return <p className="muted">{empty}</p>;
  return (
    <ul className="blocks">
      {blocks.map((block) => (
        <BlockItem key={`${block.start}-${block.kind}-${block.title}`} block={block} />
      ))}
    </ul>
  );
}

export function LoadBar({ load, label }: { load: DayLoad; label: string }) {
  const budget = Math.max(load.budget_minutes, 1);
  // El medidor se llena hasta el límite; el porcentaje leído es el real (puede pasar de 100).
  const percent = Math.round((load.planned_minutes / budget) * 100);
  return (
    <div className="load">
      <div className="load-head">
        <span>{label}</span>
        <span>
          {minutes(load.planned_minutes)} de {minutes(load.budget_minutes)}
          {load.over_budget && <strong className="over"> · por encima del límite</strong>}
        </span>
      </div>
      <meter
        min={0}
        max={budget}
        high={budget * 0.9}
        optimum={0}
        value={Math.min(load.planned_minutes, budget)}
        aria-label={`Carga de ${label}: ${percent} % del límite`}
      />
      <p className="muted small">
        Fijo {minutes(load.fixed_minutes)} · Protegido {minutes(load.protected_minutes)} · Libre{" "}
        {minutes(load.free_minutes)}
      </p>
    </div>
  );
}
