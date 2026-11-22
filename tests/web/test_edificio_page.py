"""Página del edificio (F11-T03, ADR-0022): `edificio.html` convive con la vista clásica.

Se sirve como cualquier estático compilado, con las mismas cabeceras de seguridad. Sus datos llegan
solo por la API, que exige sesión (ADR-0013): sin sesión, la página carga pero no ve nada.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.web.conftest import ORIGIN, build_web

EDIFICIO = (
    '<!doctype html><title>Edificio</title><script type="module" src="/assets/e.js"></script>'
)
CLASICA = "<!doctype html><title>LifeOS</title>"


def _dist(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(CLASICA, encoding="utf-8")
    (dist / "edificio.html").write_text(EDIFICIO, encoding="utf-8")
    return dist


@pytest.mark.security
def test_edificio_page_is_served_with_the_same_security_headers(tmp_path: Path) -> None:
    harness = build_web(tmp_path, dist=_dist(tmp_path))
    with TestClient(harness.client.app, base_url=ORIGIN) as client:
        response = client.get("/edificio.html")
    assert response.status_code == 200
    assert "<title>Edificio</title>" in response.text
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.security
def test_classic_view_stays_at_root(tmp_path: Path) -> None:
    harness = build_web(tmp_path, dist=_dist(tmp_path))
    with TestClient(harness.client.app, base_url=ORIGIN) as client:
        assert "<title>LifeOS</title>" in client.get("/").text
        assert "<title>LifeOS</title>" in client.get("/hoy").text


@pytest.mark.security
def test_edificio_data_still_requires_a_session(tmp_path: Path) -> None:
    harness = build_web(tmp_path, dist=_dist(tmp_path))
    with TestClient(harness.client.app, base_url=ORIGIN) as client:
        assert client.get("/edificio.html").status_code == 200
        assert client.get("/api/hoy").status_code == 401
        assert client.get("/api/agentes").status_code == 401
