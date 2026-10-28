# Life OS — aplicación

Repositorio de código de Life OS. La documentación de planificación (plan, reglas, ADR, specs, fases) vive en la carpeta padre `../` y no se copia aquí (ADR-0005).

## Requisitos
- Python 3.12 (`py` en Windows) y [uv](https://docs.astral.sh/uv/).

## Uso
```bash
uv sync                                    # crea .venv e instala dependencias de desarrollo
uv run pytest --cov --cov-report=term-missing
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run bandit -c pyproject.toml -r src
```

## Configuración
Copia `.env.example` a `.env` y complétalo. Las variables del entorno del proceso tienen prioridad sobre `.env`:
- `LIFEOS_VAULT_PATH`: ruta a tu bóveda real, fuera del repositorio. Las pruebas nunca la usan: crean una bóveda temporal.
- `NVIDIA_API_KEY`: clave del catálogo de NVIDIA (ADR-0007). Nunca se imprime ni se registra.
- `LIFEOS_LOGS_DIR`: carpeta de logs; vacío = `logs/` (ignorada por Git).

La configuración declarativa vive en `config/`: `permissions.yaml` (política del propietario), `models.yaml` (proveedores y perfiles) y `agents/*.yaml` (Jefe y Coach). Al arrancar se valida todo junto; cualquier exceso o clave desconocida detiene el arranque.

## Verificación real del proveedor
```bash
uv run python scripts/smoke_nvidia.py            # perfiles ligero y grande
uv run python scripts/smoke_nvidia.py --perfil ligero
```
Envía un texto fijo sin datos personales y deja un registro en `logs/usage-AAAA-MM.jsonl`. Las pruebas `live` están excluidas por defecto.

## Logs (ADR-0008)
`logs/usage-AAAA-MM.jsonl` (un registro por intento de llamada) y `logs/audit-AAAA-MM.jsonl` (escrituras y rechazos de herramientas). No contienen prompts, respuestas, contenido de notas ni claves. La purga de meses cerrados hace más de 90 días es explícita: `lifeos.adapters.jsonl_logs.purge_logs(dir, ahora, dry_run=False)`; por defecto solo simula.

## Estructura
- `src/lifeos/domain/`: entidades y reglas puras (sin E/S).
- `src/lifeos/ports/`: contratos (Protocol) que usa la aplicación.
- `src/lifeos/adapters/`: implementaciones, p. ej. la bóveda Markdown.
- `src/lifeos/application/`: casos de uso, permisos efectivos, bóveda con permisos, herramientas y pasarela de modelos.
- `src/lifeos/bootstrap.py`: raíz de composición; único módulo que conecta adaptadores con casos de uso.
- `scripts/`: utilidades manuales (verificación real del proveedor).
- `tests/`: pruebas unitarias, de integración y de seguridad (marcadores `unit`, `integration`, `security`).
