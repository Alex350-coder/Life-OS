"""Anexar un check-in a la sección `## Check-ins` de una nota diaria existente."""

from lifeos.domain.checkin import CHECKIN_HEADING, CHECKIN_SECTION, CheckInEntry
from lifeos.domain.note import append_line, insert_line, split_lines
from lifeos.domain.sections import find_section, section_insert_index
from lifeos.ports.vault import VaultPath, VaultStore


def _with_new_section(text: str, entry: str) -> str:
    lines = split_lines(text)
    if lines and lines[-1].strip():
        text = append_line(text, "")  # una línea en blanco antes del título nuevo
    return append_line(append_line(text, f"## {CHECKIN_HEADING}"), entry)


def append_checkin(vault: VaultStore, path: VaultPath, entry: CheckInEntry) -> VaultPath:
    """Inserta la entrada al final de la sección (creándola al final si falta).

    No crea la nota diaria: si no existe, se informa. Si el usuario editó la nota entre la
    lectura y la escritura, la bóveda informa un conflicto y no se reintenta.
    """
    snapshot = vault.read(path)
    span = find_section(snapshot.text, CHECKIN_SECTION)
    if span is None:
        new_text = _with_new_section(snapshot.text, entry.render())
    else:
        index = section_insert_index(snapshot.text, span)
        new_text = insert_line(snapshot.text, index, entry.render())
    vault.write(path, new_text, expected=snapshot.version)
    return path
