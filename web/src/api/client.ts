// Cliente de la API local (ADR-0013): misma origen, cookie de sesión HttpOnly y token CSRF en
// cabecera para todo lo que cambia estado. Nunca guarda tokens en localStorage.

export interface ApiFailure {
  code: string;
  message: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly data: unknown;

  constructor(status: number, failure: ApiFailure, data: unknown = null) {
    super(failure.message);
    this.name = "ApiError";
    this.status = status;
    this.code = failure.code;
    this.data = data;
  }
}

interface Envelope<T> {
  ok: boolean;
  data?: T;
  error?: ApiFailure;
}

const UNREADABLE: ApiFailure = { code: "respuesta", message: "respuesta no válida del servidor" };

async function parse<T>(response: Response): Promise<T> {
  let body: Envelope<T> | null = null;
  try {
    body = (await response.json()) as Envelope<T>;
  } catch {
    throw new ApiError(response.status, UNREADABLE);
  }
  if (!response.ok || !body.ok) {
    throw new ApiError(response.status, body.error ?? UNREADABLE, body.data ?? null);
  }
  return body.data as T;
}

let csrfToken: string | null = null;

async function csrf(): Promise<string> {
  if (csrfToken === null) {
    const session = await parse<{ csrf_token: string }>(
      await fetch("/api/session", { credentials: "same-origin" }),
    );
    csrfToken = session.csrf_token;
  }
  return csrfToken;
}

export function resetSession(): void {
  csrfToken = null;
}

export async function get<T>(path: string, params: Record<string, string | number | boolean> = {}): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== "") query.set(key, String(value));
  }
  const url = query.size > 0 ? `${path}?${query.toString()}` : path;
  return parse<T>(await fetch(url, { credentials: "same-origin" }));
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": await csrf() },
    body: JSON.stringify(body),
  });
  if (response.status === 204) return undefined as T;
  return parse<T>(response);
}

export async function checkSession(): Promise<boolean> {
  try {
    await csrf();
    return true;
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return false;
    throw error;
  }
}
