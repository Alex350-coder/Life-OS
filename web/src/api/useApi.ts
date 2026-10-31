import { useCallback, useEffect, useState } from "react";
import type { ApiError } from "./client";

export interface ApiState<T> {
  data: T | null;
  error: ApiError | Error | null;
  loading: boolean;
  reload: () => void;
}

interface Settled<T> {
  key: string;
  data: T | null;
  error: ApiError | Error | null;
}

// Carga datos de la API al montar y cada vez que cambia `key`. Sin caché propia: la bóveda es la
// fuente de verdad y cada vista muestra lo que hay ahora. `loading` se deriva: la última
// respuesta corresponde a otra clave (o a una recarga anterior).
export function useApi<T>(load: () => Promise<T>, key: string): ApiState<T> {
  const [version, setVersion] = useState(0);
  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const current = `${key}#${version}`;

  useEffect(() => {
    let active = true;
    load()
      .then((data) => {
        if (active) setSettled({ key: current, data, error: null });
      })
      .catch((reason: unknown) => {
        const error = reason instanceof Error ? reason : new Error(String(reason));
        if (active) setSettled({ key: current, data: null, error });
      });
    return () => {
      active = false;
    };
    // `load` cambia en cada render; la dependencia real es la clave (incluye la recarga).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [current]);

  const reload = useCallback(() => setVersion((n) => n + 1), []);
  return {
    data: settled?.data ?? null,
    error: settled?.error ?? null,
    loading: settled?.key !== current,
    reload,
  };
}
