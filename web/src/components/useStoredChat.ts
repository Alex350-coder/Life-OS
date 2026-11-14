import { useCallback, useState } from "react";
import type { ChatMessage } from "./ChatLog";

// DT-18: la conversación sobrevive a cerrar la pestaña. Se guarda en el almacenamiento local del
// navegador de este equipo (origen 127.0.0.1:puerto), nunca en el servidor ni en la bóveda.
// Lo que se lee se valida: un valor manipulado o de otra versión se descarta sin romper la vista.
export const MAX_STORED_MESSAGES = 100;
const AUTHORS = new Set(["Tú", "Coach", "Jefe"]);

function isMessage(value: unknown): value is ChatMessage {
  if (typeof value !== "object" || value === null) return false;
  const item = value as Record<string, unknown>;
  return (
    typeof item.id === "number" &&
    typeof item.author === "string" &&
    AUTHORS.has(item.author) &&
    Array.isArray(item.lines) &&
    item.lines.every((line) => typeof line === "string") &&
    (item.isError === undefined || typeof item.isError === "boolean")
  );
}

function load(key: string): ChatMessage[] {
  try {
    const raw = window.localStorage.getItem(key);
    if (raw === null) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed.filter(isMessage).slice(-MAX_STORED_MESSAGES) : [];
  } catch {
    return []; // almacenamiento bloqueado o JSON dañado: se empieza de cero
  }
}

function save(key: string, messages: ChatMessage[]): void {
  try {
    if (messages.length === 0) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, JSON.stringify(messages));
  } catch {
    // sin almacenamiento (modo privado, cuota): el chat sigue funcionando en memoria
  }
}

export function useStoredChat(key: string) {
  const [messages, setMessages] = useState<ChatMessage[]>(() => load(key));

  const add = useCallback(
    (author: ChatMessage["author"], lines: string[], isError = false) => {
      setMessages((current) => {
        const id = (current.at(-1)?.id ?? 0) + 1;
        const message: ChatMessage = { id, author, lines, isError };
        const next = [...current, message].slice(-MAX_STORED_MESSAGES);
        save(key, next);
        return next;
      });
    },
    [key],
  );

  const clear = useCallback(() => {
    save(key, []);
    setMessages([]);
  }, [key]);

  return { messages, add, clear };
}
