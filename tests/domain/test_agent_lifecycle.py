"""Ciclo de vida de los agentes importados (F8-T01, DT-16, spec registro-de-agentes)."""

from datetime import UTC, datetime, timedelta

import pytest

from lifeos.domain.agent_lifecycle import (
    AgentState,
    LifecycleAction,
    LifecycleError,
    RegistryEntry,
    apply_action,
    can_run,
    can_test,
    register_import,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _imported(sha: str = SHA_A, version: int = 1) -> RegistryEntry:
    return register_import(None, "universidad", sha, version, T0)


def _active() -> RegistryEntry:
    entry = _imported()
    for action in (LifecycleAction.PROBAR, LifecycleAction.APROBAR, LifecycleAction.ACTIVAR):
        entry = apply_action(entry, action, T0)
    return entry


def test_import_creates_draft_never_active() -> None:
    entry = _imported()

    assert entry.state is AgentState.BORRADOR
    assert entry.current == 1
    assert entry.current_version.sha256 == SHA_A
    assert not can_run(entry)
    assert entry.history[-1].action is LifecycleAction.IMPORTAR


def test_full_happy_path_reaches_active() -> None:
    entry = _active()

    assert entry.state is AgentState.ACTIVO
    assert can_run(entry)
    assert [t.action for t in entry.history] == [
        LifecycleAction.IMPORTAR,
        LifecycleAction.PROBAR,
        LifecycleAction.APROBAR,
        LifecycleAction.ACTIVAR,
    ]


def test_transitions_return_new_objects() -> None:
    draft = _imported()

    tested = apply_action(draft, LifecycleAction.PROBAR, T0)

    assert draft.state is AgentState.BORRADOR
    assert tested is not draft


@pytest.mark.parametrize(
    ("action", "message"),
    [
        (LifecycleAction.APROBAR, "probar"),
        (LifecycleAction.ACTIVAR, "aprobada"),
    ],
)
def test_cannot_skip_steps(action: LifecycleAction, message: str) -> None:
    with pytest.raises(LifecycleError, match=message):
        apply_action(_imported(), action, T0)


def test_deactivate_stops_runs_and_tests() -> None:
    entry = apply_action(_active(), LifecycleAction.DESACTIVAR, T0)

    assert entry.state is AgentState.DESACTIVADO
    assert not can_run(entry)
    assert not can_test(entry)
    with pytest.raises(LifecycleError, match="desactivado"):
        apply_action(entry, LifecycleAction.PROBAR, T0)


def test_deactivate_twice_is_rejected() -> None:
    entry = apply_action(_active(), LifecycleAction.DESACTIVAR, T0)

    with pytest.raises(LifecycleError, match="ya está desactivado"):
        apply_action(entry, LifecycleAction.DESACTIVAR, T0)


def test_reactivating_an_approved_version_needs_no_new_approval() -> None:
    entry = apply_action(_active(), LifecycleAction.DESACTIVAR, T0)

    entry = apply_action(entry, LifecycleAction.ACTIVAR, T0)

    assert entry.state is AgentState.ACTIVO


def test_deactivated_draft_cannot_be_activated_without_approval() -> None:
    entry = apply_action(_imported(), LifecycleAction.DESACTIVAR, T0)

    with pytest.raises(LifecycleError, match="aprobada"):
        apply_action(entry, LifecycleAction.ACTIVAR, T0)


def test_new_version_goes_back_to_draft_and_stops_running() -> None:
    entry = register_import(_active(), "universidad", SHA_B, 2, T0 + timedelta(days=1))

    assert entry.state is AgentState.BORRADOR
    assert entry.current == 2
    assert not can_run(entry)
    assert [v.number for v in entry.versions] == [1, 2]


def test_new_version_must_raise_the_file_version() -> None:
    with pytest.raises(LifecycleError, match="sube 'version'"):
        register_import(_active(), "universidad", SHA_B, 1, T0)


def test_identical_file_is_rejected_with_hint() -> None:
    with pytest.raises(LifecycleError, match="v1"):
        register_import(_active(), "universidad", SHA_A, 2, T0)


def test_import_for_other_agent_is_rejected() -> None:
    with pytest.raises(LifecycleError, match="otro agente"):
        register_import(_active(), "hobbies", SHA_B, 2, T0)


def test_restore_previously_approved_version_is_approved() -> None:
    entry = register_import(_active(), "universidad", SHA_B, 2, T0)

    restored = apply_action(entry, LifecycleAction.RESTAURAR, T0, version=1)

    assert restored.current == 1
    assert restored.current_version.sha256 == SHA_A
    assert restored.state is AgentState.APROBADO
    assert [v.number for v in restored.versions] == [1, 2]  # nada se borra


def test_restore_never_approved_version_is_draft() -> None:
    entry = register_import(_imported(), "universidad", SHA_B, 2, T0)

    restored = apply_action(entry, LifecycleAction.RESTAURAR, T0, version=1)

    assert restored.state is AgentState.BORRADOR


@pytest.mark.parametrize(("version", "message"), [(None, "indica la versión"), (1, "ya es")])
def test_restore_requires_another_existing_version(version: int | None, message: str) -> None:
    with pytest.raises(LifecycleError, match=message):
        apply_action(_imported(), LifecycleAction.RESTAURAR, T0, version=version)


def test_restore_unknown_version_is_rejected() -> None:
    with pytest.raises(LifecycleError, match="no existe"):
        apply_action(_imported(), LifecycleAction.RESTAURAR, T0, version=7)


def test_version_argument_only_for_restore() -> None:
    with pytest.raises(LifecycleError, match="solo"):
        apply_action(_imported(), LifecycleAction.PROBAR, T0, version=1)


def test_import_action_is_not_a_transition() -> None:
    with pytest.raises(LifecycleError, match="importar"):
        apply_action(_imported(), LifecycleAction.IMPORTAR, T0)


def test_approval_is_per_file_hash_not_per_number() -> None:
    # v2 aprobada, luego v3; restaurar v2 conserva su aprobación, restaurar v1 (nunca aprobada) no.
    entry = register_import(_imported(), "universidad", SHA_B, 2, T0)
    for action in (LifecycleAction.PROBAR, LifecycleAction.APROBAR):
        entry = apply_action(entry, action, T0)
    entry = register_import(entry, "universidad", SHA_C, 3, T0)

    assert apply_action(entry, LifecycleAction.RESTAURAR, T0, version=2).state is (
        AgentState.APROBADO
    )
    assert apply_action(entry, LifecycleAction.RESTAURAR, T0, version=1).state is (
        AgentState.BORRADOR
    )


@pytest.mark.parametrize("bad", ["", "x" * 64, "A" * 64, "a" * 63])
def test_sha_must_be_lowercase_hex_sha256(bad: str) -> None:
    with pytest.raises(LifecycleError, match="huella"):
        register_import(None, "universidad", bad, 1, T0)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(LifecycleError, match="zona"):
        register_import(None, "universidad", SHA_A, 1, datetime(2026, 11, 11))


def test_invalid_agent_id_is_rejected() -> None:
    with pytest.raises(LifecycleError, match="id"):
        register_import(None, "Universidad!", SHA_A, 1, T0)


def test_retesting_a_tested_draft_is_allowed() -> None:
    entry = apply_action(_imported(), LifecycleAction.PROBAR, T0)

    assert apply_action(entry, LifecycleAction.PROBAR, T0).state is AgentState.PROBADO


def test_activating_an_active_agent_is_rejected() -> None:
    with pytest.raises(LifecycleError, match="ya está activo"):
        apply_action(_active(), LifecycleAction.ACTIVAR, T0)
