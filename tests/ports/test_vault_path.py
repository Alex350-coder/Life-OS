"""SEC-02 (validación léxica): rutas relativas, normalizadas y sin escapes."""

import pytest

from lifeos.ports.vault import PathRejectedError, VaultPath

pytestmark = [pytest.mark.unit, pytest.mark.security]


@pytest.mark.parametrize(
    "raw",
    ["00-Inbox/captura.md", "01-Universidad/Redes/notas.md", "Semanal/2026-W44.md", "a.md"],
)
def test_accepts_normal_relative_paths(raw: str) -> None:
    assert VaultPath.parse(raw).value == raw


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "../fuera.md",
        "a/../../fuera.md",
        "a/..",
        "./a.md",
        "a//b.md",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "C:relativo.md",
        "c:\\Windows\\win.ini",
        "a\\..\\..\\x.md",
        "\\\\servidor\\share\\x.md",
        "nota.md:secreto",
        "a/CON.md",
        "nul",
        "com1.txt",
        "LPT9",
        "a/nota.md.",
        "a/nota.md ",
        "a\x00b.md",
        "a/",
    ],
)
def test_rejects_traversal_absolute_drive_unc_ads_and_reserved(raw: str) -> None:
    with pytest.raises(PathRejectedError):
        VaultPath.parse(raw)


def test_parts_parent_and_join() -> None:
    path = VaultPath.parse("01-Universidad/Redes/notas.md")

    assert path.parts == ("01-Universidad", "Redes", "notas.md")
    assert path.parent == VaultPath.parse("01-Universidad/Redes")
    assert VaultPath.parse("Archivo").join(path) == VaultPath.parse(
        "Archivo/01-Universidad/Redes/notas.md"
    )


def test_is_under_compares_whole_components() -> None:
    path = VaultPath.parse("Archivo/a.md")

    assert path.is_under(VaultPath.parse("Archivo"))
    assert not VaultPath.parse("Archivos/a.md").is_under(VaultPath.parse("Archivo"))
    assert VaultPath.parse("Archivo").parent is None


def test_relative_to_strips_prefix() -> None:
    path = VaultPath.parse("Archivo/01-Universidad/a.md")

    assert path.relative_to(VaultPath.parse("Archivo")) == VaultPath.parse("01-Universidad/a.md")
    with pytest.raises(PathRejectedError):
        path.relative_to(VaultPath.parse("Semanal"))
