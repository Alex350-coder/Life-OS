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
  version: number;
  data: T | null;
  error: ApiError | Error | null;
}

// Carga datos de la API al montar y cada vez que cambia `key`. Sin caché propia: la bóveda es la
// fuente de verdad y cada vista muestra lo que hay ahora.
// - Al cambiar `key` (otra semana, otros filtros) no se muestran los datos de la clave anterior.
// - Al recargar la misma clave se mantienen los datos actuales hasta que llegan los nuevos.
export function useApi<T>(load: () => Promise<T>, key: string): ApiState<T> {
  const [version, setVersion] = useState(0);
  const [settled, setSettled] = useState<Settled<T> | null>(null);

  useEffect(() => {
    let active = true;
    load()
      .then((data) => {
        if (active) setSettled({ key, version, data, error: null });
      })
      .catch((reason: unknown) => {
        const error = reason instanceof Error ? reason : new Error(String(reason));
        if (active) setSettled({ key, version, data: null, error });
      });
    return () => {
      active = false;
    };
    // `load` cambia en cada render; las dependencias reales son la clave y la recarga.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, version]);

  const reload = useCallback(() => setVersion((n) => n + 1), []);
  const current = settled !== null && settled.key === key ? settled : null;
  return {
    data: current?.data ?? null,
    error: current?.error ?? null,
    loading: current === null || current.version !== version,
    reload,
  };
}
