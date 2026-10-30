"""Dobles de prueba aislados (nunca se usan fuera de tests): reloj y cliente LLM con guion."""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from lifeos.ports.llm import LlmError, LlmRequest, LlmResponse, TokenUsage
from lifeos.ports.logs import AuditRecord, AuditResult, LogWriteError, UsageRecord

FIXED_NOW = datetime(2026, 10, 27, 9, 30, tzinfo=UTC)


@dataclass
class FakeClock:
    """Reloj controlado: `sleep` avanza el tiempo sin esperar de verdad."""

    current: datetime = FIXED_NOW
    elapsed: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> datetime:
        return self.current + timedelta(seconds=self.elapsed)

    def monotonic(self) -> float:
        return self.elapsed

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.elapsed += seconds

    def advance(self, seconds: float) -> None:
        self.elapsed += seconds


def ok_response(
    text: str = '{"ok": true}',
    *,
    prompt: int | None = 12,
    completion: int | None = 5,
    finish_reason: str | None = "stop",
    model: str = "proveedor/modelo-ligero",
) -> LlmResponse:
    return LlmResponse(
        text=text,
        usage=TokenUsage(prompt=prompt, completion=completion),
        model=model,
        finish_reason=finish_reason,
        http_status=200,
    )


Step = LlmResponse | LlmError | Callable[[LlmRequest], LlmResponse]


@dataclass
class FakeLlmClient:
    """Cliente LLM que reproduce un guion de respuestas o errores y registra las peticiones."""

    script: list[Step]
    provider: str = "nvidia"
    clock: FakeClock | None = None
    latency_s: float = 0.0
    requests: list[LlmRequest] = field(default_factory=list)

    def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if self.clock is not None:
            self.clock.advance(self.latency_s)
        if not self.script:
            raise AssertionError("el guion del cliente falso se agotó")
        step = self.script.pop(0)
        if isinstance(step, LlmError):
            raise step
        if isinstance(step, LlmResponse):
            return step
        return step(request)


@dataclass
class MemoryUsageLog:
    """Registro de uso en memoria; `fail` simula un disco bloqueado (p. ej., OneDrive)."""

    records: list[UsageRecord] = field(default_factory=list)
    fail: bool = False

    def append(self, record: UsageRecord) -> None:
        if self.fail:
            raise LogWriteError("registro de uso no disponible")
        self.records.append(record)


@dataclass
class MemoryAuditLog:
    records: list[AuditRecord] = field(default_factory=list)
    fail_on: AuditResult | None = None

    def append(self, record: AuditRecord) -> None:
        if record.result is self.fail_on:
            raise LogWriteError("auditoría no disponible")
        self.records.append(record)
