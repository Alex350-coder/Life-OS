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
// El registro existe desde el principio (así se anuncia el primer mensaje) y solo se anuncian
// las respuestas del agente: el eco de lo que escribe el propietario no se lee en voz alta.
export function ChatLog({ label, messages }: Props) {
  return (
    // El registro tiene scroll propio: necesita foco para leerlo con el teclado (WCAG 2.1.1).
    // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
    <div className="chat" role="log" aria-label={label} aria-relevant="additions" tabIndex={0}>
      <ol className="chat-list">
      {messages.length === 0 && <li className="muted">Todavía no hay mensajes.</li>}
      {messages.map((message) => (
        <li
          key={message.id}
          className={`msg msg-${message.author === "Tú" ? "me" : "agent"}`}
          aria-hidden={message.author === "Tú" ? true : undefined}
        >
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
    </div>
  );
}
