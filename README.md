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
Copia `.env.example` a `.env` y completa `LIFEOS_VAULT_PATH` con la ruta a tu bóveda real, que vive fuera del repositorio. Las pruebas nunca usan la bóveda real: crean una bóveda temporal.

## Estructura
- `src/lifeos/domain/`: entidades y reglas puras (sin E/S).
- `src/lifeos/ports/`: contratos (Protocol) que usa la aplicación.
- `src/lifeos/adapters/`: implementaciones, p. ej. la bóveda Markdown.
- `src/lifeos/application/`: casos de uso.
- `tests/`: pruebas unitarias, de integración y de seguridad (marcadores `unit`, `integration`, `security`).
