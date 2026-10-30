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
- `LIFEOS_TIMEZONE` (opcional): zona IANA; si se indica, debe coincidir con la de `_sistema/horario.md`.
- Un `.env` mal formado detiene el arranque con un error de configuración (nunca muestra valores).

La configuración declarativa vive en `config/`: `permissions.yaml` (política del propietario), `models.yaml` (proveedores y perfiles) y `agents/*.yaml` (Jefe y Coach). Al arrancar se valida todo junto; cualquier exceso o clave desconocida detiene el arranque.

## Uso diario (Fase 3, ADR-0006 y ADR-0009)
Antes de planificar, completa en tu bóveda `_sistema/horario.md` (plantilla en `../vault-template/_sistema/horario.md`, formato en `../docs/specs/horario-y-limites.md`). Sin `foco_max_dia`, `libre_min_dia` y `margen`, el Jefe no planifica (D-13).

```bash
uv run lifeos capturar "Idea para el proyecto"   # nota nueva en 00-Inbox/
uv run lifeos tareas --area universidad          # filtros: --area, --estado, --texto, --archivadas
uv run lifeos semana --semana 2026-W45           # propone y guarda Semanal/2026-W45.md como "propuesto"
uv run lifeos aprobar --semana 2026-W45          # revalida y publica Semanal/ y Diario/ como "aprobado"
uv run lifeos semana --ia                        # propuesta con el modelo del Jefe (requiere NVIDIA_API_KEY)
```

- Toda propuesta (heurística o IA) pasa por el validador determinista; un plan inválido no se guarda y se muestran sus conflictos (código de salida 1).
- Puedes revisar y ajustar los bloques de `Semanal/AAAA-Wnn.md` antes de aprobar: `aprobar` los vuelve a validar con la bóveda y la hora actuales y publica la versión canónica del plan.
- Un plan aprobado no se reemplaza: se revisa con `semana --replanificar` (ver abajo). Las notas diarias conservan `Check-ins`, `Cierre` y tus propias secciones.
- Códigos de salida: 0 correcto, 1 rechazo o error del caso de uso, 2 error de uso o de configuración.

## Ciclo diario (Fase 4, Hito H1)
```bash
uv run lifeos hoy                                    # bloque actual, siguiente, tiempo protegido y check-ins
uv run lifeos checkin "Empiezo" --estado empezando --tarea t-7f3k2m
uv run lifeos checkin --estado bloqueado             # micro-paso de 10 minutos como máximo
uv run lifeos checkin --estado cansado               # propone al Jefe "reducir carga hoy"
uv run lifeos checkin --estado perdido --tarea t-7f3k2m   # propone reubicar el bloque (ya empezado)
uv run lifeos checkin "Nota" --capturar "Idea suelta"     # además guarda una captura en 00-Inbox/
uv run lifeos cierre --hecho t-aaaaaa --avance t-bbbbbb=40 --restante t-bbbbbb=1h --no t-cccccc
uv run lifeos cierre ... --confirmar                 # solo con --confirmar se aplican los cambios
uv run lifeos semana --replanificar                  # propone Semanal/AAAA-Wnn-rN.md desde ahora
uv run lifeos aprobar                                # aprueba la revisión (solo hoy y los días siguientes)
uv run lifeos uso --mes 2026-11                      # resumen del log de uso (no necesita la bóveda)
```

- **Check-ins.** El Coach solo anexa en `## Check-ins` y crea capturas. Un `hecho` o el silencio nunca completan una tarea: el avance se confirma en el cierre.
- **Propuestas al Jefe.** Son líneas `ajuste:` del check-in (ADR-0011). Con `--ia`, el modelo solo redacta el mensaje; si falla, se usa la respuesta sin IA.
- **Cierre.** Sin `--confirmar` muestra los cambios y no toca nada.
  - Con `--confirmar` actualiza solo lo respondido, anexa el resumen en `## Cierre` y propone reubicar los bloques no realizados que ya empezaron.
  - Las tareas sin respuesta no cambian.
  - Se registra una vez por día; si hay que corregir algo, se hace a mano.
- **Replanificación.**
  - La revisión conserva tal cual lo que empezó antes del corte. Un plan aprobado nunca se reescribe y los días pasados no se republican.
  - Haz el cierre antes de replanificar: el trabajo hecho y no confirmado se vuelve a planificar.
- **Cerca de medianoche.** `hoy`, `checkin` y `cierre` usan el día local de la hora actual. Pasada la medianoche corresponden al día nuevo, así que cierra antes.

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
- `src/lifeos/domain/`: también horario, capacidad, validador de planes, heurística y notas de plan (Fase 3).
- `src/lifeos/application/`: casos de uso, permisos efectivos, bóveda con permisos, herramientas, pasarela de modelos, caso de uso del Jefe (`jefe.py`, con replanificación), propuesta con IA (`jefe_ai.py`) y publicación (`plan_publication.py`). Además, Coach (`coach.py`, `coach_ai.py`), cierre diario (`closing.py`) y resumen de uso (`usage_report.py`) (Fase 4).
- `src/lifeos/domain/`: además, reglas del Coach, propuestas de ajuste, cierre y replanificación (`coach.py`, `adjustments.py`, `closing.py`, `replan.py`).
- `src/lifeos/cli/`: CLI `lifeos` (presentación, sin reglas de negocio).
- `src/lifeos/bootstrap.py`: raíz de composición; único módulo que conecta adaptadores con casos de uso.
- `scripts/`: utilidades manuales (verificación real del proveedor).
- `tests/`: pruebas unitarias, de integración y de seguridad (marcadores `unit`, `integration`, `security`).
