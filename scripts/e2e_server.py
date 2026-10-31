"""Servidor para las pruebas punta a punta de la web (Playwright, Fase 5). Solo para pruebas.

Arranca la aplicación real (`create_app`) sobre una copia de `config/` y una bóveda temporal con
datos INVENTADOS, con el reloj fijo el lunes 2026-11-02 07:30 (America/Lima) y un token de
arranque conocido. Nunca usa la bóveda, los logs ni el `.env` del propietario. Al terminar
borra la carpeta temporal.

    uv run python scripts/e2e_server.py      # escucha en 127.0.0.1:8799
"""

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import uvicorn

from lifeos.bootstrap import Settings
from lifeos.web.app import create_app
from lifeos.web.security import LOOPBACK, LaunchToken

PORT = 8799
TOKEN = "token-e2e-inventado-" + "x" * 24  # inventado; solo vale para este servidor de pruebas
PROJECT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 11, 2, 7, 30, tzinfo=ZoneInfo("America/Lima"))

HORARIO = """---
tipo: horario
zona: America/Lima
---

# Horario y límites

## Límites
- [foco_max_dia:: 4h] [libre_min_dia:: 1h] [margen:: 20%] [bloque_minimo:: 30m] [bloque_maximo:: 2h]

## Disponibilidad
- lun-vie 08:00–20:00
- sáb 09:00–13:00

## Compromisos fijos
- lun,mié 10:00–12:00 · Clase de redes [estado:: activa]

## Tiempo protegido
- mar,jue 18:00–19:30 · Gym
- dom todo el día · Descanso
"""

TASKS = (
    "- [ ] Informe de redes [id:: t-aaaaaa] [area:: universidad] [esfuerzo:: 2h] 📅 2026-11-04\n"
    "- [ ] Leer capítulo [id:: t-bbbbbb] [area:: universidad] [esfuerzo:: 1h]\n"
    "- [ ] <img src=x onerror=alert(1)> [id:: t-cccccc] [area:: universidad] [esfuerzo:: 30m]"
    " 📅 2026-11-05\n"
)


class FixedClock:
    def now(self) -> datetime:
        return NOW

    def monotonic(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        return None


def _workspace(base: Path) -> Settings:
    root = base / "project"
    shutil.copytree(PROJECT / "config", root / "config")
    vault = base / "vault"
    (vault / "_sistema").mkdir(parents=True)
    (vault / "_sistema" / "horario.md").write_text(HORARIO, encoding="utf-8")
    (vault / "01-Universidad").mkdir()
    (vault / "01-Universidad" / "redes.md").write_text(TASKS, encoding="utf-8")
    environ = {"LIFEOS_VAULT_PATH": str(vault), "LIFEOS_LOGS_DIR": str(root / "logs")}
    return Settings.from_env(root, environ)


def main() -> None:
    base = Path(tempfile.mkdtemp(prefix="lifeos-e2e-"))
    try:
        app = create_app(
            _workspace(base),
            port=PORT,
            clock=FixedClock(),
            launch_token=LaunchToken(lambda: 0.0, value=TOKEN),
            dist_dir=PROJECT / "web" / "dist",
        )
        uvicorn.run(app, host=LOOPBACK, port=PORT, access_log=False, log_level="warning")
    finally:
        shutil.rmtree(base, ignore_errors=True)


if __name__ == "__main__":
    main()
