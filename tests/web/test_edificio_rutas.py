"""Inventario de rutas del edificio (F11, ADR-0022).

`web/src/lifeos-edificio/rutas.json` es la lista de TODAS las rutas de la API. Vitest comprueba que
cada una tiene un botón o una ventana en el edificio; esta prueba comprueba que la lista coincide
con las rutas reales del servidor. Si se añade una ruta,
fallan las dos hasta que el edificio la ofrezca.
"""

import json
from pathlib import Path

from fastapi.routing import APIRouter

from lifeos.web.agents_routes import router as agents_router
from lifeos.web.calendar_routes import router as calendar_router
from lifeos.web.indicators_routes import router as indicators_router
from lifeos.web.routes import router as base_router

INVENTORY = Path(__file__).resolve().parents[2] / "web" / "src" / "lifeos-edificio" / "rutas.json"


def _routes(*routers: APIRouter) -> set[str]:
    found: set[str] = set()
    for router in routers:
        for route in router.routes:
            methods = sorted(getattr(route, "methods", None) or [])
            found.update(f"{method} {getattr(route, 'path', '')}" for method in methods)
    return {r for r in found if not r.startswith("HEAD ")}


def test_edificio_inventory_matches_every_api_route() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assert len(inventory) == len(set(inventory)), "rutas repetidas en rutas.json"
    actual = _routes(base_router, agents_router, indicators_router, calendar_router)
    assert set(inventory) == actual
