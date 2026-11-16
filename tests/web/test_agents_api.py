"""API del registro de agentes y de las propuestas (Fase 8; PLAN §9: formularios de alta,
edición e importación). Misma sesión, CSRF y Origin que el resto (SEC-11)."""

from pathlib import Path
from typing import Any

import pytest

from tests.web.conftest import ORIGIN, PROJECT, Web

pytestmark = [pytest.mark.integration, pytest.mark.security]

TEMPLATE = (PROJECT / "config" / "especialistas" / "universidad.yaml").read_text(encoding="utf-8")
DUE = "\U0001f4c5"


def _post(web: Web, path: str, body: object, csrf: str) -> Any:
    return web.client.post(path, json=body, headers={"X-CSRF-Token": csrf, "Origin": ORIGIN})


def _data(response: Any) -> Any:
    body = response.json()
    assert body["ok"], body
    return body["data"]


def _import(web: Web, csrf: str, text: str = TEMPLATE) -> Any:
    return _post(
        web, "/api/agentes/importacion", {"nombre": "universidad.yaml", "contenido": text}, csrf
    )


def _activate(web: Web, csrf: str) -> None:
    assert _import(web, csrf).status_code == 201
    assert _post(web, "/api/agentes/universidad/prueba", {}, csrf).status_code == 200
    for action in ("aprobar", "activar"):
        response = _post(web, "/api/agentes/universidad/estado", {"accion": action}, csrf)
        assert response.status_code == 200, response.text


def test_import_returns_draft_with_effective_permissions(web: Web) -> None:
    csrf = web.login()

    response = _import(web, csrf)

    assert response.status_code == 201
    agent = _data(response)
    assert agent["id"] == "universidad"
    assert agent["state"] == "borrador"
    assert agent["area"] == "universidad"
    assert agent["effective"]["reads"] == ["01-Universidad/"]
    assert agent["effective"]["writes"] == [
        {"root": "Propuestas/", "mode": "crear", "section": None}
    ]
    assert agent["versions"][0]["sha256"] == agent["versions"][0]["sha256"][:12]


def test_listing_includes_core_and_imported_agents(web: Web) -> None:
    csrf = web.login()
    _import(web, csrf)

    data = _data(web.client.get("/api/agentes"))

    assert {a["id"] for a in data["agents"]} == {"jefe", "coach"}
    assert [a["id"] for a in data["imported"]] == ["universidad"]


def test_rejected_import_lists_every_violation(web: Web) -> None:
    csrf = web.login()
    greedy = TEMPLATE.replace("    - 01-Universidad/\n", "    - 01-Universidad/\n    - 05-Salud/\n")

    response = _import(web, csrf, greedy)

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "agente_rechazado"
    assert any("05-Salud" in v for v in body["data"]["violations"])


def test_malformed_yaml_is_422_without_echoing_content(web: Web) -> None:
    csrf = web.login()

    response = _import(web, csrf, "id: [<script>alert(1)</script>\n")

    assert response.status_code == 422
    assert "<script>" not in response.text


def test_oversized_import_is_rejected_at_the_boundary(web: Web) -> None:
    csrf = web.login()

    response = _import(web, csrf, "#" * (33 * 1024))

    assert response.status_code == 422
    assert _import(web, csrf, "#" * (65 * 1024)).status_code == 413


def test_import_requires_csrf(web: Web) -> None:
    web.login()

    response = web.client.post(
        "/api/agentes/importacion", json={"nombre": "u.yaml", "contenido": TEMPLATE}
    )

    assert response.status_code == 403


def test_lifecycle_run_and_review_through_the_api(web: Web) -> None:
    csrf = web.login()
    web.write(
        "01-Universidad/examen.md",
        f"- [ ] Examen [id:: t-abc234] [area:: universidad] [esfuerzo:: 2h] {DUE} 2026-11-03\n",
    )
    _activate(web, csrf)

    run = _post(web, "/api/agentes/universidad/ejecucion", {"semana": "2026-W45"}, csrf)
    assert run.status_code == 200, run.text
    assert _data(run)["path"] == "Propuestas/2026-W45/universidad.md"

    pending = _data(web.client.get("/api/propuestas", params={"semana": "2026-W45"}))
    refs = [p["ref"] for p in pending["items"]]
    target = next(p for p in pending["items"] if p["task_id"] == "t-abc234")
    assert target["current_priority"] == "media" and target["priority"] == "alta"

    accepted = _post(web, "/api/propuestas/aceptacion", {"ref": target["ref"]}, csrf)
    assert accepted.status_code == 200, accepted.text
    assert "[prioridad:: alta]" in web.note("01-Universidad/examen.md")
    left = _data(web.client.get("/api/propuestas", params={"semana": "2026-W45"}))["items"]
    assert len(left) == len(refs) - 1


def test_deactivated_agent_cannot_run(web: Web) -> None:
    csrf = web.login()
    _activate(web, csrf)
    _post(web, "/api/agentes/universidad/estado", {"accion": "desactivar"}, csrf)

    response = _post(web, "/api/agentes/universidad/ejecucion", {}, csrf)

    assert response.status_code == 409
    assert "desactivado" in response.json()["error"]["message"]


def test_export_returns_the_exact_file(web: Web) -> None:
    csrf = web.login()
    _import(web, csrf)

    data = _data(web.client.get("/api/agentes/universidad/exportacion"))

    assert data["contenido"] == TEMPLATE
    assert data["version"] == 1


def test_restore_requires_a_version(web: Web) -> None:
    csrf = web.login()
    _import(web, csrf)

    response = _post(web, "/api/agentes/universidad/estado", {"accion": "restaurar"}, csrf)

    assert response.status_code == 409


@pytest.mark.parametrize("agent_id", ["..", "UNI", "a" * 40])
def test_agent_id_in_path_is_validated(web: Web, agent_id: str) -> None:
    csrf = web.login()

    response = _post(web, f"/api/agentes/{agent_id}/prueba", {}, csrf)

    assert response.status_code in (404, 405, 422)


def test_unknown_action_is_422(web: Web) -> None:
    csrf = web.login()
    _import(web, csrf)

    response = _post(web, "/api/agentes/universidad/estado", {"accion": "probar"}, csrf)

    assert response.status_code == 422


def test_bad_proposal_ref_is_422(web: Web) -> None:
    csrf = web.login()

    response = _post(web, "/api/propuestas/rechazo", {"ref": "../../_sistema/p1"}, csrf)

    assert response.status_code == 422


def test_proposals_without_any_folder_are_empty(web: Web, tmp_path: Path) -> None:
    web.login()

    assert _data(web.client.get("/api/propuestas"))["items"] == []
