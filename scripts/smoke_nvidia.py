"""Llamada real de verificación al proveedor configurado (F2-T01, SEC-08).

Uso (desde `project/`, con NVIDIA_API_KEY en `.env` o en el entorno):
    uv run python scripts/smoke_nvidia.py [--perfil ligero|grande|todos]

Envía un texto fijo sin datos personales (RULES F.6), valida la respuesta con un esquema
estricto y deja un registro por intento en `logs/usage-AAAA-MM.jsonl`. Solo imprime el
resultado, el modelo, la latencia y los tokens: nunca la clave ni la respuesta completa.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

from lifeos.adapters.schemas import SCHEMAS
from lifeos.application.llm_calls import CallContext
from lifeos.bootstrap import Settings, build_runtime
from lifeos.domain.errors import LifeOSError
from lifeos.ports.llm import ChatMessage

PROJECT = Path(__file__).resolve().parent.parent
PROMPT = (
    ChatMessage(
        "system",
        'Responde únicamente con un objeto JSON con la forma {"texto": "..."} y nada más.',
    ),
    ChatMessage("user", 'Devuelve {"texto": "prueba de conexión"}.'),
)


def _last_record(logs_dir: Path, correlation_id: str) -> dict[str, object] | None:
    for path in sorted(logs_dir.glob("usage-*.jsonl"), reverse=True):
        for line in reversed(path.read_text(encoding="utf-8").splitlines()):
            record = json.loads(line)
            if record.get("correlation_id") == correlation_id:
                return dict(record)
    return None


def _run(profile: str) -> bool:
    settings = Settings.from_env(PROJECT)
    runtime = build_runtime(settings, with_llm=True)
    gateway = runtime.gateway
    if gateway is None:  # build_runtime(with_llm=True) siempre la construye
        raise RuntimeError("no se construyó la pasarela de modelos")
    correlation_id = f"smoke-{uuid.uuid4().hex[:12]}"
    ctx = CallContext(
        agent_id="sistema", ritual="smoke", profile=profile, correlation_id=correlation_id
    )
    try:
        gateway.complete_structured(ctx, PROMPT, SCHEMAS["captura_v1"])
        ok = True
    except LifeOSError as error:
        print(f"[{profile}] error: {type(error).__name__}: {error}")
        ok = False
    record = _last_record(settings.logs_dir, correlation_id)
    if record is not None:
        print(
            f"[{profile}] resultado={record['outcome']} modelo={record['model']} "
            f"latencia_ms={record['latency_ms']} tokens_in={record['tokens_prompt']} "
            f"tokens_out={record['tokens_completion']} costo={record['cost_status']}"
        )
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--perfil", default="todos", choices=["ligero", "grande", "todos"])
    args = parser.parse_args()
    profiles = ["ligero", "grande"] if args.perfil == "todos" else [args.perfil]
    try:
        results = [_run(profile) for profile in profiles]
    except LifeOSError as error:
        print(f"configuración no válida: {type(error).__name__}: {error}")
        return 2
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
