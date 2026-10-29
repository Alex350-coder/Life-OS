"""Anexar entradas a una sección de una nota diaria existente (`## Check-ins`, `## Cierre`)."""

from collections.abc import Sequence

from lifeos.domain.checkin import CHECKIN_HEADING, CHECKIN_SECTION, CheckInEntry
from lifeos.domain.note import append_line, insert_line, split_lines
from lifeos.domain.sections import find_section, section_insert_index
from lifeos.ports.vault import VaultPath, VaultStore


def _with_new_section(text: str, heading: str, entries: Sequence[str]) -> str:
    lines = split_lines(text)
    if lines and lines[-1].strip():
        text = append_line(text, "")  # una línea en blanco antes del título nuevo
    text = append_line(text, f"## {heading}")
    for entry in entries:
        text = append_line(text, entry)
    return text


def append_to_section(
    vault: VaultStore,
    path: VaultPath,
    section: str,
    heading: str,
    entries: Sequence[CheckInEntry],
) -> VaultPath:
    """Inserta las entradas al final de la sección (creándola al final si falta).

    No crea la nota: si no existe, se informa. Si el usuario editó la nota entre la lectura y
    la escritura, la bóveda informa un conflicto y no se reintenta.
    """
    rendered = [entry.render() for entry in entries]
    snapshot = vault.read(path)
    span = find_section(snapshot.text, section)
    if span is None:
        new_text = _with_new_section(snapshot.text, heading, rendered)
    else:
        index = section_insert_index(snapshot.text, span)
        new_text = snapshot.text
        for offset, line in enumerate(rendered):
            new_text = insert_line(new_text, index + offset, line)
    vault.write(path, new_text, expected=snapshot.version)
    return path


def append_checkin(vault: VaultStore, path: VaultPath, entry: CheckInEntry) -> VaultPath:
    return append_to_section(vault, path, CHECKIN_SECTION, CHECKIN_HEADING, (entry,))
