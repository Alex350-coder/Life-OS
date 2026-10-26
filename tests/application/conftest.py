"""Helpers de los casos de uso; los fixtures `root` y `vault` están en tests/conftest.py."""

from collections.abc import Callable

import pytest

from lifeos.adapters.filesystem_vault import FilesystemVault
from lifeos.ports.vault import VaultPath

WriteNote = Callable[[str, str], None]


@pytest.fixture
def write_note(vault: FilesystemVault) -> WriteNote:
    def _write(path: str, text: str) -> None:
        vault.write(VaultPath.parse(path), text, expected=None)

    return _write
