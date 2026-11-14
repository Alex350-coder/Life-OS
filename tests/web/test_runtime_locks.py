"""DT-09: una llamada larga a la IA no bloquea las escrituras normales de la web."""

import threading
from pathlib import Path

import pytest

from tests.web.conftest import Web

pytestmark = pytest.mark.integration


def _finishes(action: object, timeout_s: float = 2.0) -> bool:
    done = threading.Event()

    def run() -> None:
        action()  # type: ignore[operator]
        done.set()

    threading.Thread(target=run, daemon=True).start()
    return done.wait(timeout_s)


def test_a_plain_write_does_not_wait_for_an_ai_operation(web: Web) -> None:
    runtime = web.client.app.state.web  # type: ignore[attr-defined]

    def plain_write() -> None:
        with runtime.writing():
            pass

    with runtime.writing(use_ai=True):  # una propuesta con IA en curso (decenas de segundos)
        assert _finishes(plain_write)


def test_two_ai_operations_still_run_one_at_a_time(web: Web) -> None:
    runtime = web.client.app.state.web  # type: ignore[attr-defined]

    def second_ai() -> None:
        with runtime.writing(use_ai=True):
            pass

    with runtime.writing(use_ai=True):
        assert not _finishes(second_ai, timeout_s=0.2)


def test_two_log_writers_on_the_same_file_share_one_lock(tmp_path: Path) -> None:
    # El runtime con IA crea sus propios logs: deben serializar con los del runtime base.
    from lifeos.adapters.jsonl_logs import JsonlAuditLog

    first = JsonlAuditLog(tmp_path, secrets=())
    second = JsonlAuditLog(tmp_path, secrets=())
    assert first._lock is second._lock
