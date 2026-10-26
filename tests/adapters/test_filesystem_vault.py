"""Adaptador de bóveda en disco: confinamiento (SEC-02), atomicidad y concurrencia (SEC-03)."""

import os
import sys
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.ports.vault import (
    NoteExistsError,
    NoteNotFoundError,
    PathRejectedError,
    VaultIOError,
    VaultPath,
    Version,
    VersionConflictError,
)

pytestmark = pytest.mark.integration

NOTE = VaultPath.parse("01-Universidad/redes.md")


def _leftover_temp_files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.name.startswith(".lifeos-")]


# --- Lectura, creación y listado -------------------------------------------------------------


def test_create_then_read_returns_same_text_and_version(vault: FilesystemVault) -> None:
    version = vault.write(NOTE, "- [ ] Leer\n", expected=None)

    snapshot = vault.read(NOTE)

    assert snapshot.text == "- [ ] Leer\n"
    assert snapshot.version == version
    assert snapshot.path == NOTE


def test_read_missing_note_raises(vault: FilesystemVault) -> None:
    with pytest.raises(NoteNotFoundError):
        vault.read(NOTE)


def test_exists_reports_presence(vault: FilesystemVault) -> None:
    assert not vault.exists(NOTE)
    vault.write(NOTE, "x", expected=None)
    assert vault.exists(NOTE)


def test_version_depends_only_on_content(vault: FilesystemVault) -> None:
    first = vault.write(NOTE, "igual", expected=None)
    other = vault.write(VaultPath.parse("b.md"), "igual", expected=None)

    assert first == other
    assert isinstance(first, Version)


def test_list_notes_returns_sorted_markdown_only(vault: FilesystemVault, root: Path) -> None:
    vault.write(VaultPath.parse("b.md"), "", expected=None)
    vault.write(VaultPath.parse("a/c.md"), "", expected=None)
    (root / "imagen.png").write_bytes(b"\x89PNG")

    assert vault.list_notes() == (VaultPath.parse("a/c.md"), VaultPath.parse("b.md"))
    assert vault.list_notes(VaultPath.parse("a")) == (VaultPath.parse("a/c.md"),)
    assert vault.list_notes(VaultPath.parse("no-existe")) == ()


def test_preserves_crlf_and_bom_byte_for_byte(vault: FilesystemVault, root: Path) -> None:
    raw = "﻿- [ ] Uno\r\n- [ ] Dos\r\n".encode()
    (root / "crlf.md").write_bytes(raw)
    path = VaultPath.parse("crlf.md")

    snapshot = vault.read(path)
    vault.write(path, snapshot.text, expected=snapshot.version)

    assert (root / "crlf.md").read_bytes() == raw


def test_non_utf8_note_raises_io_error(vault: FilesystemVault, root: Path) -> None:
    (root / "latin1.md").write_bytes("canción".encode("latin-1"))

    with pytest.raises(VaultIOError):
        vault.read(VaultPath.parse("latin1.md"))


def test_root_must_be_an_existing_directory(tmp_path: Path) -> None:
    with pytest.raises(VaultIOError):
        FilesystemVault(tmp_path / "no-existe")


# --- SEC-03: concurrencia optimista ---------------------------------------------------------


@pytest.mark.security
def test_create_over_existing_note_is_rejected(vault: FilesystemVault) -> None:
    vault.write(NOTE, "original", expected=None)

    with pytest.raises(NoteExistsError):
        vault.write(NOTE, "pisado", expected=None)
    assert vault.read(NOTE).text == "original"


@pytest.mark.security
def test_update_with_current_version_succeeds(vault: FilesystemVault) -> None:
    version = vault.write(NOTE, "v1", expected=None)

    new_version = vault.write(NOTE, "v2", expected=version)

    assert vault.read(NOTE).text == "v2"
    assert new_version != version


@pytest.mark.security
def test_external_change_between_read_and_write_is_detected(
    vault: FilesystemVault, root: Path
) -> None:
    vault.write(NOTE, "v1", expected=None)
    snapshot = vault.read(NOTE)
    (root / NOTE.value).write_text("editado en Obsidian", encoding="utf-8")

    with pytest.raises(VersionConflictError):
        vault.write(NOTE, "v2", expected=snapshot.version)
    assert (root / NOTE.value).read_text(encoding="utf-8") == "editado en Obsidian"


@pytest.mark.security
def test_update_of_deleted_note_is_a_conflict(vault: FilesystemVault, root: Path) -> None:
    version = vault.write(NOTE, "v1", expected=None)
    (root / NOTE.value).unlink()

    with pytest.raises(VersionConflictError):
        vault.write(NOTE, "v2", expected=version)


# --- SEC-03: atomicidad ---------------------------------------------------------------------


@pytest.mark.security
def test_failure_during_replace_keeps_original_and_cleans_temp(
    vault: FilesystemVault, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = vault.write(NOTE, "original", expected=None)

    def boom(*_args: object) -> None:
        raise OSError("disco lleno")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(VaultIOError):
        vault.write(NOTE, "nuevo", expected=version)

    assert (root / NOTE.value).read_text(encoding="utf-8") == "original"
    assert _leftover_temp_files(root) == []


@pytest.mark.security
def test_failure_mid_write_keeps_original_and_cleans_temp(
    vault: FilesystemVault, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = vault.write(NOTE, "original", expected=None)

    def boom(_fd: int) -> None:
        raise OSError("fallo de E/S")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(VaultIOError):
        vault.write(NOTE, "nuevo", expected=version)

    assert (root / NOTE.value).read_text(encoding="utf-8") == "original"
    assert _leftover_temp_files(root) == []


@pytest.mark.security
def test_transient_permission_error_on_replace_is_retried(
    vault: FilesystemVault, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = vault.write(NOTE, "original", expected=None)
    real_replace = os.replace
    calls = {"n": 0}

    def flaky(src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError("archivo bloqueado por sincronización")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    vault.write(NOTE, "nuevo", expected=version)

    assert vault.read(NOTE).text == "nuevo"
    assert calls["n"] == 2


@pytest.mark.security
def test_persistent_permission_error_surfaces_as_io_error(
    vault: FilesystemVault, root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = vault.write(NOTE, "original", expected=None)

    def locked(*_args: object) -> None:
        raise PermissionError("bloqueado")

    monkeypatch.setattr(os, "replace", locked)
    with pytest.raises(VaultIOError, match=r"bloqueado|permiso"):
        vault.write(NOTE, "nuevo", expected=version)
    assert _leftover_temp_files(root) == []


# --- SEC-02: confinamiento con enlaces ------------------------------------------------------


def _symlink_or_skip(link: Path, target: Path, *, is_dir: bool) -> None:
    try:
        link.symlink_to(target, target_is_directory=is_dir)
    except (OSError, NotImplementedError) as exc:  # Windows sin modo desarrollador
        pytest.skip(f"no se pueden crear symlinks: {exc}")


@pytest.mark.security
def test_rejects_file_symlink_escaping_root(
    vault: FilesystemVault, root: Path, tmp_path: Path
) -> None:
    secret = tmp_path / "secreto.md"
    secret.write_text("fuera", encoding="utf-8")
    _symlink_or_skip(root / "enlace.md", secret, is_dir=False)

    with pytest.raises(PathRejectedError):
        vault.read(VaultPath.parse("enlace.md"))
    with pytest.raises(PathRejectedError):
        vault.write(VaultPath.parse("enlace.md"), "x", expected=None)


@pytest.mark.security
def test_rejects_symlinked_directory_even_inside_root(vault: FilesystemVault, root: Path) -> None:
    (root / "real").mkdir()
    _symlink_or_skip(root / "alias", root / "real", is_dir=True)

    with pytest.raises(PathRejectedError):
        vault.write(VaultPath.parse("alias/nota.md"), "x", expected=None)


@pytest.mark.security
@pytest.mark.skipif(sys.platform != "win32", reason="las junctions solo existen en Windows")
def test_rejects_junction_escaping_root(vault: FilesystemVault, root: Path, tmp_path: Path) -> None:
    import _winapi  # módulo solo disponible en Windows

    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "nota.md").write_text("fuera", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(root / "puente"))

    with pytest.raises(PathRejectedError):
        vault.read(VaultPath.parse("puente/nota.md"))
    with pytest.raises(PathRejectedError):
        vault.write(VaultPath.parse("puente/nueva.md"), "x", expected=None)
    assert not (outside / "nueva.md").exists()


@pytest.mark.security
def test_list_notes_skips_links(vault: FilesystemVault, root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "fuera"
    outside.mkdir()
    (outside / "x.md").write_text("fuera", encoding="utf-8")
    vault.write(VaultPath.parse("dentro.md"), "", expected=None)
    _symlink_or_skip(root / "enlace", outside, is_dir=True)

    assert vault.list_notes() == (VaultPath.parse("dentro.md"),)
