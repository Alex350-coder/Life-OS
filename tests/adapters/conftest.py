"""Servidor HTTP local con respuestas programadas para probar el cliente LLM sin red externa."""

import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@dataclass(frozen=True)
class Reply:
    status: int = 200
    body: bytes = b""
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "application/json"),)
    delay_s: float = 0.0  # espera antes de responder
    chunk_delay_s: float = 0.0  # espera entre fragmentos del cuerpo (respuesta lenta)
    chunks: int = 1


@dataclass(frozen=True)
class Received:
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class StubServer:
    base_url: str
    replies: list[Reply] = field(default_factory=list)
    received: list[Received] = field(default_factory=list)


def _handler(stub: StubServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return  # silencio: los registros del servidor no interesan en las pruebas

        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            stub.received.append(Received(self.path, dict(self.headers.items()), body))
            reply = stub.replies.pop(0) if stub.replies else Reply(status=599)
            time.sleep(reply.delay_s)
            try:
                self.send_response(reply.status)
                for name, value in reply.headers:
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                size = max(1, len(reply.body) // reply.chunks)
                for start in range(0, len(reply.body), size):
                    self.wfile.write(reply.body[start : start + size])
                    self.wfile.flush()
                    time.sleep(reply.chunk_delay_s)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return  # el cliente cortó por timeout: es lo que la prueba espera

        do_POST = _serve
        do_GET = _serve

    return Handler


@pytest.fixture
def stub_server() -> Iterator[StubServer]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler(StubServer("")))
    stub = StubServer(f"http://127.0.0.1:{server.server_address[1]}/v1")
    server.RequestHandlerClass = _handler(stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield stub
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
