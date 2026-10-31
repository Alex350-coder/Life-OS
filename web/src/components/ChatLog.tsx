export interface ChatMessage {
  id: number;
  author: "Tú" | "Coach" | "Jefe";
  lines: string[];
  isError?: boolean;
}

type Props = {
  label: string;
  messages: ChatMessage[];
};

// Conversación en memoria de la pestaña: lo que importa ya quedó en la bóveda (check-ins y
// planes), así que no se guarda aparte.
export function ChatLog({ label, messages }: Props) {
  if (messages.length === 0) {
    return <p className="muted">Todavía no hay mensajes.</p>;
  }
  return (
    <ol className="chat" aria-label={label} aria-live="polite">
      {messages.map((message) => (
        <li key={message.id} className={`msg msg-${message.author === "Tú" ? "me" : "agent"}`}>
          <span className="msg-author">{message.author}</span>
          <div className={message.isError === true ? "msg-body msg-error" : "msg-body"}>
            {message.lines.map((line, index) => (
              // El orden de las líneas de un mensaje no cambia nunca: el índice es estable.
              <p key={index}>{line}</p>
            ))}
          </div>
        </li>
      ))}
    </ol>
  );
}
