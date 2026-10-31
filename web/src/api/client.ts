// Cliente de la API local (ADR-0013): mismo origen, cookie de sesión HttpOnly y token CSRF en
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

interface Envelope {
  ok?: unknown;
  data?: unknown;
  error?: ApiFailure;
}

const UNREADABLE: ApiFailure = { code: "respuesta", message: "respuesta no válida del servidor" };

// Avisa a la App cuando el servidor dice que no hay sesión (caducó o se reinició).
const sessionListeners = new Set<() => void>();

export function onSessionLost(listener: () => void): () => void {
  sessionListeners.add(listener);
  return () => sessionListeners.delete(listener);
}

async function parse<T>(response: Response): Promise<T> {
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    throw new ApiError(response.status, UNREADABLE);
  }
  if (typeof body !== "object" || body === null) throw new ApiError(response.status, UNREADABLE);
  const envelope = body as Envelope;
  if (!response.ok || envelope.ok !== true) {
    if (response.status === 401) sessionListeners.forEach((listener) => listener());
    throw new ApiError(response.status, envelope.error ?? UNREADABLE, envelope.data ?? null);
  }
  return envelope.data as T;
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

async function send(path: string, body: unknown): Promise<Response> {
  return fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": await csrf() },
    body: JSON.stringify(body),
  });
}

export async function post<T>(path: string, body: unknown): Promise<T> {
  let response = await send(path, body);
  if (response.status === 403) {
    // Otra pestaña abrió una sesión nueva: el token en memoria quedó viejo. Se renueva una vez.
    const failure = (await response.clone().json().catch(() => null)) as Envelope | null;
    if (failure?.error?.code === "csrf") {
      resetSession();
      response = await send(path, body);
    }
  }
  return parse<T>(response);
}

export async function postVoid(path: string, body: unknown): Promise<void> {
  const response = await send(path, body);
  if (response.status !== 204) await parse<unknown>(response);
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
