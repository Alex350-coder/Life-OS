"""Contenido malicioso de notas (criterio de la Fase 5): sale como dato JSON, nunca como HTML.

La API solo devuelve `application/json`; el texto conserva los `<`, `>` y comillas (el frontend
los muestra como texto) y pierde los caracteres invisibles que podrían disfrazarlo.
"""

import pytest

from tests.web.conftest import ORIGIN, Web

pytestmark = [pytest.mark.security, pytest.mark.integration]

PAYLOADS = (
    "<img src=x onerror=alert(1)>",
    "<script>alert('x')</script>",
    '"><svg/onload=alert(1)>',
    "javascript:alert(1)",
)
RLO = chr(0x202E)


def _evil_tasks() -> str:
    return "".join(
        f"- [ ] {payload} [id:: t-xss{n}aa] [area:: universidad] [esfuerzo:: 1h]\n"
        for n, payload in zip("2345", PAYLOADS, strict=True)
    )


def test_malicious_task_titles_are_returned_as_plain_json_text(web: Web) -> None:
    web.write("01-Universidad/xss.md", _evil_tasks())
    web.login()
    response = web.client.get("/api/tareas", params={"texto": ""})
    assert response.headers["content-type"].startswith("application/json")
    titles = {task["title"] for task in response.json()["data"]}
    for payload in PAYLOADS:
        assert payload in titles


def test_invisible_characters_are_removed_from_note_text(web: Web) -> None:
    web.write(
        "01-Universidad/bidi.md",
        f"- [ ] Pagar{RLO}gnp.exe [id:: t-b2d2aa] [area:: universidad] [esfuerzo:: 1h]\n",
    )
    web.login()
    titles = [task["title"] for task in web.client.get("/api/tareas").json()["data"]]
    assert "Pagargnp.exe" in titles
    assert all(RLO not in title for title in titles)


def test_malicious_capture_is_stored_as_text_and_answered_as_json(web: Web) -> None:
    csrf = web.login()
    response = web.client.post(
        "/api/capturas",
        json={"texto": PAYLOADS[0]},
        headers={"X-CSRF-Token": csrf, "Origin": ORIGIN},
    )
    assert response.status_code == 201
    assert response.headers["content-type"].startswith("application/json")
    assert PAYLOADS[0] in web.note(response.json()["data"]["path"])


def test_html_is_never_served_from_the_api(web: Web) -> None:
    web.write("01-Universidad/xss.md", _evil_tasks())
    web.login()
    for path in ("/api/tareas", "/api/semana", "/api/historial", "/api/agentes"):
        content_type = web.client.get(path).headers["content-type"]
        assert content_type.startswith("application/json"), path
