"""Almacén JSON del registro de agentes (F8-T01, ADR-0018)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lifeos.adapters.agent_store import JsonAgentRegistry
from lifeos.domain.agent_lifecycle import (
    AgentState,
    LifecycleAction,
    RegistryEntry,
    apply_action,
    register_import,
)
from lifeos.ports.state import StateError

pytestmark = pytest.mark.integration

T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)


def _entry() -> RegistryEntry:
    entry = register_import(None, "universidad", "a" * 64, 1, T0)
    entry = apply_action(entry, LifecycleAction.PROBAR, T0)
    return register_import(entry, "universidad", "b" * 64, 2, T0)


def test_missing_registry_is_empty(tmp_path: Path) -> None:
    assert JsonAgentRegistry(tmp_path / "agentes").load() == {}


def test_round_trip_preserves_entry(tmp_path: Path) -> None:
    store = JsonAgentRegistry(tmp_path)
    entry = _entry()

    store.save(entry)

    assert JsonAgentRegistry(tmp_path).load() == {"universidad": entry}


def test_save_keeps_other_agents(tmp_path: Path) -> None:
    store = JsonAgentRegistry(tmp_path)
    store.save(_entry())
    store.save(register_import(None, "hobbies", "c" * 64, 1, T0))

    assert sorted(store.load()) == ["hobbies", "universidad"]


def test_version_copy_round_trip(tmp_path: Path) -> None:
    store = JsonAgentRegistry(tmp_path)

    store.put_version("universidad", 1, b"id: universidad\n")

    assert store.get_version("universidad", 1) == b"id: universidad\n"
    assert (tmp_path / "universidad" / "v1.yaml").is_file()


def test_same_version_same_bytes_is_idempotent(tmp_path: Path) -> None:
    store = JsonAgentRegistry(tmp_path)
    store.put_version("universidad", 1, b"x")

    store.put_version("universidad", 1, b"x")


def test_version_copy_is_never_overwritten(tmp_path: Path) -> None:
    store = JsonAgentRegistry(tmp_path)
    store.put_version("universidad", 1, b"original")

    with pytest.raises(StateError, match="ya existe"):
        store.put_version("universidad", 1, b"otro")

    assert store.get_version("universidad", 1) == b"original"


def test_missing_version_copy_is_explicit(tmp_path: Path) -> None:
    with pytest.raises(StateError, match="no se encontró"):
        JsonAgentRegistry(tmp_path).get_version("universidad", 3)


@pytest.mark.parametrize(("agent_id", "number"), [("../x", 1), ("UNI", 1), ("uni", 0)])
def test_unsafe_names_are_rejected(tmp_path: Path, agent_id: str, number: int) -> None:
    with pytest.raises(StateError, match="no válid"):
        JsonAgentRegistry(tmp_path).put_version(agent_id, number, b"x")


@pytest.mark.parametrize(
    "content",
    [
        "no es json",
        "[]",
        '{"version": 1, "agentes": {"universidad": {"state": "volando"}}}',
        '{"version": 2, "agentes": {}}',
        '{"version": 1, "agentes": {"otro": ' + '{"agent_id": "universidad"}}}',
    ],
)
def test_corrupt_registry_is_an_explicit_error(tmp_path: Path, content: str) -> None:
    (tmp_path / "registro.json").write_text(content, encoding="utf-8")

    with pytest.raises(StateError, match=r"registro\.json"):
        JsonAgentRegistry(tmp_path).load()


def test_registry_file_shape_is_readable(tmp_path: Path) -> None:
    JsonAgentRegistry(tmp_path).save(_entry())

    data = json.loads((tmp_path / "registro.json").read_text(encoding="utf-8"))

    agent = data["agentes"]["universidad"]
    assert data["version"] == 1
    assert agent["state"] == AgentState.BORRADOR
    assert agent["current"] == 2
    assert [v["sha256"][:1] for v in agent["versions"]] == ["a", "b"]
    assert [h["action"] for h in agent["history"]] == ["importar", "probar", "importar"]


def test_inconsistent_current_version_is_rejected(tmp_path: Path) -> None:
    JsonAgentRegistry(tmp_path).save(_entry())
    path = tmp_path / "registro.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["agentes"]["universidad"]["current"] = 9
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(StateError, match=r"registro\.json"):
        JsonAgentRegistry(tmp_path).load()


def test_tampered_hash_in_registry_is_rejected(tmp_path: Path) -> None:
    JsonAgentRegistry(tmp_path).save(_entry())
    path = tmp_path / "registro.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["agentes"]["universidad"]["versions"][0]["sha256"] = "zz"
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(StateError, match=r"registro\.json"):
        JsonAgentRegistry(tmp_path).load()


# --- Aprobación por versión y migración (F10-T03, DT-28) ----------------------------------------


def test_round_trip_keeps_version_approval(tmp_path: Path) -> None:
    entry = register_import(None, "universidad", "a" * 64, 1, T0)
    for action in (LifecycleAction.PROBAR, LifecycleAction.APROBAR):
        entry = apply_action(entry, action, T0)
    store = JsonAgentRegistry(tmp_path)

    store.save(entry)

    loaded = JsonAgentRegistry(tmp_path).load()["universidad"]
    assert loaded.versions[0].approved
    assert loaded == entry


def test_old_registry_without_approved_flag_derives_it_from_history(tmp_path: Path) -> None:
    entry = register_import(None, "universidad", "a" * 64, 1, T0)
    for action in (LifecycleAction.PROBAR, LifecycleAction.APROBAR):
        entry = apply_action(entry, action, T0)
    entry = register_import(entry, "universidad", "b" * 64, 2, T0)
    JsonAgentRegistry(tmp_path).save(entry)
    path = tmp_path / "registro.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    for version in raw["agentes"]["universidad"]["versions"]:
        del version["approved"]  # formato de la Fase 8
    path.write_text(json.dumps(raw), encoding="utf-8")

    loaded = JsonAgentRegistry(tmp_path).load()["universidad"]

    assert [v.approved for v in loaded.versions] == [True, False]


def test_approved_flag_must_be_a_boolean(tmp_path: Path) -> None:
    JsonAgentRegistry(tmp_path).save(_entry())
    path = tmp_path / "registro.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["agentes"]["universidad"]["versions"][0]["approved"] = "sí"
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(StateError, match="dañado"):
        JsonAgentRegistry(tmp_path).load()


def test_registry_stays_small_after_thousands_of_transitions(tmp_path: Path) -> None:
    entry = register_import(None, "universidad", "a" * 64, 1, T0)
    for action in (LifecycleAction.PROBAR, LifecycleAction.APROBAR, LifecycleAction.ACTIVAR):
        entry = apply_action(entry, action, T0)
    for _ in range(2_000):  # antes, ~20 000 transiciones superaban los 2 MiB y no cargaba
        entry = apply_action(entry, LifecycleAction.DESACTIVAR, T0)
        entry = apply_action(entry, LifecycleAction.ACTIVAR, T0)
    store = JsonAgentRegistry(tmp_path)

    store.save(entry)

    assert (tmp_path / "registro.json").stat().st_size < 64 * 1024
    assert store.load()["universidad"].state is AgentState.ACTIVO
