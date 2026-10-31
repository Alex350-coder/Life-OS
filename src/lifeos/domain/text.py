"""Texto que se muestra al propietario (CLI y web): sin caracteres que alteren lo que se ve."""

import unicodedata

# Control, formato (bidi), sustitutos, uso privado, sin asignar y separadores de línea.
_HIDDEN = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def printable(line: str) -> str:
    """Un título de la bóveda o un texto del modelo no puede manipular la terminal ni disfrazar
    lo que muestra (p. ej., con caracteres bidi o secuencias de escape)."""
    return "".join(ch for ch in line if unicodedata.category(ch) not in _HIDDEN)
