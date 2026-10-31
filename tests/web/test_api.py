"""Contrato de la API local (F5-T02…T04): mismos casos de uso que la CLI, de punta a punta.

Planificar → aprobar → hoy → check-in → cierre → replanificar sobre una bóveda temporal con datos
inventados. Cada respuesta refleja lo que hay en la bóveda: no hay datos simulados.
"""

from datetime import datetime
from typing import Any

import pytest

from tests.planning_builders import MON, at
from tests.web.conftest import ORIGIN, Web

pytestmark = [pytest.mark.integration]

WEEK = "2026-W45"
MONDAY = "Diario/2026-11-02.md"


def _json(response: Any) -> Any:
    return response.json()


def _post(web: Web, csrf: str, path: str, body: object) -> Any:
    return web.client.post(path, json=body, headers={"X-CSRF-Token": csrf, "Origin": ORIGIN})


def _at(web: Web, moment: datetime) -> None:
    web.clock.current, web.clock.elapsed = moment, 0.0


@pytest.fixture
def csrf(web: Web) -> str:
    return web.login()


def _approved(web: Web, csrf: str) -> None:
    assert _post(web, csrf, "/api/plan/propuesta", {"semana": WEEK}).status_code == 200
    assert _post(web, csrf, "/api/plan/aprobacion", {"semana": WEEK}).status_code == 200


# --- Tareas y capturas -----------------------------------------------------------------------


def test_tasks_come_from_the_vault(web: Web, csrf: str) -> None:
    body = _json(web.client.get("/api/tareas"))
    assert body["ok"] is True
    titles = {task["title"] for task in body["data"]}
    assert titles == {"Informe de redes", "Leer capítulo", "Práctica de SQL"}
    report = next(t for t in body["data"] if t["id"] == "t-aaaaaa")
    assert report == {
        "id": "t-aaaaaa",
        "title": "Informe de redes",
        "status": "propuesta",
        "area": "universidad",
        "effort": "3h",
        "progress": "25%",
        "priority": "media",
        "due": "2026-11-04",
        "scheduled": None,
        "path": "01-Universidad/redes.md",
        "archived": False,
    }


def test_task_filters_are_validated(web: Web, csrf: str) -> None:
    assert len(_json(web.client.get("/api/tareas", params={"texto": "sql"}))["data"]) == 1
    wrong = web.client.get("/api/tareas", params={"area": "marte"})
    assert wrong.status_code == 422
    assert _json(wrong)["error"]["code"] == "entrada_invalida"


def test_capture_writes_to_the_inbox(web: Web, csrf: str) -> None:
    response = _post(web, csrf, "/api/capturas", {"texto": "Comprar cuaderno"})
    assert response.status_code == 201
    path = _json(response)["data"]["path"]
    assert path.startswith("00-Inbox/")
    assert "Comprar cuaderno" in web.note(path)
    assert "web" in web.note(path)  # origen de la captura


@pytest.mark.parametrize(
    "body", [{}, {"texto": ""}, {"texto": "x" * 2001}, {"texto": "a", "extra": 1}, {"texto": 3}]
)
def test_capture_body_is_strict(web: Web, csrf: str, body: object) -> None:
    response = _post(web, csrf, "/api/capturas", body)
    assert response.status_code == 422
    assert "x" * 50 not in response.text  # el valor recibido no se repite


def test_upcoming_tasks_use_the_local_day(web: Web, csrf: str) -> None:
    _at(web, at(MON, "09:00"))
    data = _json(web.client.get("/api/tareas/proximas", params={"dias": 7}))["data"]
    assert [task["id"] for task in data] == ["t-aaaaaa"]
    assert web.client.get("/api/tareas/proximas", params={"dias": 61}).status_code == 422


# --- Jefe: semana, propuesta y aprobación ------------------------------------------------------


def test_week_without_plan_shows_load(web: Web, csrf: str) -> None:
    data = _json(web.client.get("/api/semana", params={"semana": WEEK}))["data"]
    assert data["status"] is None and data["blocks"] == []
    assert len(data["load"]) == 7 and data["days"][0] == "2026-11-02"
    assert data["load"][0]["fixed_minutes"] == 120


def test_bad_week_is_422(web: Web, csrf: str) -> None:
    assert web.client.get("/api/semana", params={"semana": "2026-45"}).status_code == 422


def test_propose_then_approve_publishes_the_plan(web: Web, csrf: str) -> None:
    proposed = _json(_post(web, csrf, "/api/plan/propuesta", {"semana": WEEK}))["data"]
    assert proposed["accepted"] and proposed["status"] == "propuesto"
    assert proposed["path"] == f"Semanal/{WEEK}.md"
    assert proposed["proposal"]["work"]
    week = _json(web.client.get("/api/semana", params={"semana": WEEK}))["data"]
    assert week["status"] == "propuesto"
    assert sum(day["planned_minutes"] for day in week["load"]) > 0
    approved = _json(_post(web, csrf, "/api/plan/aprobacion", {"semana": WEEK}))["data"]
    assert approved["status"] == "aprobado"
    assert "estado_plan: aprobado" in web.note(MONDAY)


def test_approving_twice_is_409(web: Web, csrf: str) -> None:
    _approved(web, csrf)
    again = _post(web, csrf, "/api/plan/aprobacion", {"semana": WEEK})
    assert again.status_code == 409
    assert "replanificar" in _json(again)["error"]["message"]


def test_invalid_edited_plan_is_rejected_with_conflicts(web: Web, csrf: str) -> None:
    _post(web, csrf, "/api/plan/propuesta", {"semana": WEEK})
    weekly = web.note(f"Semanal/{WEEK}.md")
    # Un bloque de trabajo sobre la clase de redes (compromiso fijo del lunes).
    lines = weekly.splitlines()
    first_work = next(i for i, line in enumerate(lines) if "[id:: t-" in line)
    task_part = lines[first_work].split(" · ", 1)[1]
    lines.insert(first_work, f"- 10:00–11:00 · {task_part}")
    web.write(f"Semanal/{WEEK}.md", "\n".join(lines) + "\n")
    response = _post(web, csrf, "/api/plan/aprobacion", {"semana": WEEK})
    assert response.status_code == 422
    body = _json(response)
    assert body["error"]["code"] == "plan_rechazado"
    assert body["data"]["accepted"] is False and body["data"]["conflicts"]
    assert "estado_plan: aprobado" not in web.client.get("/api/semana").text


def test_ai_without_provider_key_is_a_configuration_error(web: Web, csrf: str) -> None:
    response = _post(web, csrf, "/api/plan/propuesta", {"semana": WEEK, "ia": True})
    assert response.status_code == 503
    assert _json(response)["error"]["code"] == "configuracion"
    assert "NVIDIA_API_KEY" in _json(response)["error"]["message"]


# --- Coach: hoy y check-ins --------------------------------------------------------------------


def test_today_without_plan_is_409(web: Web, csrf: str) -> None:
    _at(web, at(MON, "08:30"))
    response = web.client.get("/api/hoy")
    assert response.status_code == 409
    assert "semana" in _json(response)["error"]["message"]


def test_today_and_checkin_follow_the_approved_plan(web: Web, csrf: str) -> None:
    _approved(web, csrf)
    _at(web, at(MON, "08:30"))
    today = _json(web.client.get("/api/hoy"))["data"]
    assert today["day"] == "2026-11-02" and today["approved"] is True
    assert today["current"] is not None and today["current"]["kind"] == "trabajo"
    task_id = today["current"]["task_id"]
    reply = _post(
        web,
        csrf,
        "/api/checkins",
        {"texto": "Me cuesta empezar", "estado": "bloqueado", "tarea": task_id},
    )
    assert reply.status_code == 201
    data = _json(reply)["data"]
    assert data["kind"] and data["micro_step"]["minutes"] <= 10
    assert "Me cuesta empezar" in web.note(MONDAY)
    assert _json(web.client.get("/api/hoy"))["data"]["checkins"] == 1


def test_tired_checkin_proposes_an_adjustment(web: Web, csrf: str) -> None:
    _approved(web, csrf)
    _at(web, at(MON, "08:30"))
    data = _json(_post(web, csrf, "/api/checkins", {"estado": "cansado"}))["data"]
    assert data["adjustment"]["kind"] == "reducir carga hoy"
    assert len(_json(web.client.get("/api/hoy"))["data"]["adjustments"]) == 1


@pytest.mark.parametrize(
    "body",
    [{"estado": "feliz"}, {"tarea": "t-1"}, {"estado": "nota", "otra": True}, {"ia": "si"}],
)
def test_checkin_body_is_validated(web: Web, csrf: str, body: object) -> None:
    _approved(web, csrf)
    _at(web, at(MON, "08:30"))
    before = web.note(MONDAY)
    assert _post(web, csrf, "/api/checkins", body).status_code == 422
    assert web.note(MONDAY) == before


# --- Cierre y replanificación --------------------------------------------------------------


def test_closing_preview_changes_nothing_and_confirm_applies(web: Web, csrf: str) -> None:
    _approved(web, csrf)
    _at(web, at(MON, "20:30"))
    answers = {"respuestas": [{"tarea": "t-aaaaaa", "tipo": "parcial", "avance": "60"}]}
    tasks_before = web.note("01-Universidad/redes.md")
    preview = _json(_post(web, csrf, "/api/cierre/vista-previa", answers))["data"]
    assert preview["changes"][0]["progress_after"] == "60%"
    assert web.note("01-Universidad/redes.md") == tasks_before
    applied = _json(_post(web, csrf, "/api/cierre", answers))["data"]
    assert applied["written"]
    assert "[avance:: 60%]" in web.note("01-Universidad/redes.md")
    assert _post(web, csrf, "/api/cierre", answers).status_code == 409  # uno por día


@pytest.mark.parametrize(
    "answer",
    [
        {"tarea": "t-aaaaaa", "tipo": "parcial"},
        {"tarea": "t-aaaaaa", "tipo": "hecha", "avance": "10"},
        {"tarea": "t-aaaaaa", "tipo": "quizas"},
        {"tarea": "t-aaaaaa", "tipo": "parcial", "avance": "140"},
    ],
)
def test_closing_answers_are_validated(web: Web, csrf: str, answer: object) -> None:
    _approved(web, csrf)
    _at(web, at(MON, "20:30"))
    response = _post(web, csrf, "/api/cierre", {"respuestas": [answer]})
    assert response.status_code == 422


def test_repeated_answer_for_the_same_task_is_422(web: Web, csrf: str) -> None:
    _approved(web, csrf)
    _at(web, at(MON, "20:30"))
    twice = [{"tarea": "t-aaaaaa", "tipo": "hecha"}, {"tarea": "t-aaaaaa", "tipo": "no"}]
    assert _post(web, csrf, "/api/cierre", {"respuestas": twice}).status_code == 422


def test_replan_proposes_a_revision(web: Web, csrf: str) -> None:
    _approved(web, csrf)
    _at(web, at(MON, "08:30"))
    _post(web, csrf, "/api/checkins", {"estado": "cansado"})
    revision = _json(_post(web, csrf, "/api/plan/revision", {"semana": WEEK}))["data"]
    assert revision["path"] == f"Semanal/{WEEK}-r1.md" and revision["status"] == "propuesto"
    week = _json(web.client.get("/api/semana", params={"semana": WEEK}))["data"]
    assert week["revision"] == 1 and week["versions"] == 2


# --- Historial, agentes y uso --------------------------------------------------------------


def test_history_shows_the_audited_writes(web: Web, csrf: str) -> None:
    _approved(web, csrf)
    data = _json(web.client.get("/api/historial"))["data"]
    actions = {(e["actor"], e["action"], e["result"]) for e in data["entries"]}
    assert ("jefe", "publicar_plan", "ok") in actions
    assert all(e["result"] != "intento" for e in data["entries"])
    assert web.client.get("/api/historial", params={"limite": 0}).status_code == 422


def test_agents_are_read_only_with_effective_permissions(web: Web, csrf: str) -> None:
    data = _json(web.client.get("/api/agentes", params={"mes": "2026-11"}))["data"]
    assert {a["id"] for a in data["agents"]} == {"coach", "jefe"}
    jefe = next(a for a in data["agents"] if a["id"] == "jefe")
    assert "publicar_plan" in jefe["tools"] and jefe["plan_writes"]
    assert data["plan_owner"] == "jefe"
    for method in ("post", "put", "patch", "delete"):
        response = getattr(web.client, method)(
            "/api/agentes", headers={"X-CSRF-Token": csrf, "Origin": ORIGIN}
        )
        assert response.status_code == 405


def test_usage_month_is_validated(web: Web, csrf: str) -> None:
    data = _json(web.client.get("/api/uso", params={"mes": "2026-11"}))["data"]
    assert data["rows"] == [] and data["total"]["calls"] == 0
    assert web.client.get("/api/uso", params={"mes": "2026-13"}).status_code == 422


def test_unknown_api_route_is_404_json(web: Web, csrf: str) -> None:
    response = web.client.get("/api/no-existe")
    assert response.status_code == 404 and _json(response)["ok"] is False
