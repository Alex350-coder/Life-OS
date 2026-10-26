"""Regresiones de la revisión de la Fase 1 (puerto y adaptador de bóveda)."""

import os
import tempfile
from pathlib import Path

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.ports.vault import (
    NoteExistsError,
    PathRejectedError,
    VaultIOError,
    VaultPath,
    Version,
)

pytestmark = [pytest.mark.security]


@pytest.mark.parametrize(
    "raw",
    [
        "COM¹.md",
        "a/LPT².md",
        "CONIN$",
        "conout$.md",
        "ＣＯＮ.md",
        "a​.md",
        "a /b.md",
        ".obsidian/workspace.md",
        "a/.lifeos-tmp.md",
        "a/" + "b" * 256 + ".md",
        "/".join(["carpeta"] * 200) + "/a.md",
    ],
)
def test_vault_path_rejects_windows_lookalikes_hidden_and_overlong(raw: str) -> None:
    with pytest.raises(PathRejectedError):
        VaultPath.parse(raw)


@pytest.mark.parametrize("digest", ["", "abc", "Z" * 64])
def test_version_requires_sha256_hex(digest: str) -> None:
    with pytest.raises(ValueError, match="versión"):
        Version(digest)


def test_list_notes_includes_uppercase_extension(tmp_path: Path) -> None:
    (tmp_path / "Nota.MD").write_text("x", encoding="utf-8")

    assert FilesystemVault(tmp_path).list_notes() == (VaultPath.parse("Nota.MD"),)


def test_list_notes_surfaces_unreadable_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "a.md").write_text("x", encoding="utf-8")
    real_walk = os.walk

    def walk_with_error(top: str, **kwargs: object):  # type: ignore[no-untyped-def]
        onerror = kwargs.get("onerror")
        assert callable(onerror), "list_notes debe pasar onerror a os.walk"
        onerror(PermissionError("sin acceso a 05-Salud"))
        yield from real_walk(top)

    monkeypatch.setattr(os, "walk", walk_with_error)
    with pytest.raises(VaultIOError, match="sin acceso"):
        FilesystemVault(tmp_path).list_notes()


def test_temp_cleanup_failure_does_not_mask_original_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = FilesystemVault(tmp_path)
    path = VaultPath.parse("a.md")
    vault.write(path, "x", expected=None)

    def unlink_fails(self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("antivirus")

    monkeypatch.setattr(Path, "unlink", unlink_fails)
    with pytest.raises(NoteExistsError):
        vault.write(path, "y", expected=None)


def test_mkstemp_failure_is_wrapped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_space(**_kwargs: object) -> tuple[int, str]:
        raise OSError("disco lleno")

    monkeypatch.setattr(tempfile, "mkstemp", no_space)
    with pytest.raises(VaultIOError, match="disco lleno"):
        FilesystemVault(tmp_path).write(VaultPath.parse("a.md"), "x", expected=None)
