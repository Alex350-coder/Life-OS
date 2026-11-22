"""La interfaz de LifeOS es el edificio (ADR-0022, ADR-0023): `index.html` en la raíz.

Se sirve como cualquier estático compilado, con las mismas cabeceras de seguridad. Sus datos llegan
solo por la API, que exige sesión (ADR-0013): sin sesión, la página carga pero no ve nada.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.web.conftest import ORIGIN, build_web

EDIFICIO = '<!doctype html><title>Life OS</title><canvas id="world"></canvas>'


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(EDIFICIO, encoding="utf-8")
    return dist


@pytest.mark.security
def test_the_building_is_served_at_root_with_the_security_headers(tmp_path: Path) -> None:
    harness = build_web(tmp_path, dist=_dist(tmp_path))
    with TestClient(harness.client.app, base_url=ORIGIN) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert 'canvas id="world"' in response.text
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.security
def test_building_data_still_requires_a_session(tmp_path: Path) -> None:
    harness = build_web(tmp_path, dist=_dist(tmp_path))
    with TestClient(harness.client.app, base_url=ORIGIN) as client:
        assert client.get("/").status_code == 200
        assert client.get("/api/hoy").status_code == 401
        assert client.get("/api/agentes").status_code == 401
