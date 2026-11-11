"""Áreas de vida y sus carpetas en la bóveda (vault-template, aditional-context §7.1).

Las rutas se devuelven como texto relativo a la raíz de la bóveda; los casos de uso las
convierten en `VaultPath`, que valida el formato.
"""

from types import MappingProxyType

from lifeos.domain.task import Area

AREA_FOLDERS = MappingProxyType(
    {
        Area.UNIVERSIDAD: "01-Universidad",
        Area.PROYECTOS: "02-Proyectos",
        Area.CARRERA: "03-Carrera",
        Area.HOBBIES: "04-Hobbies",
        Area.SALUD: "05-Salud",
        Area.FINANZAS: "06-Finanzas",
        Area.COTIDIANO: "07-Cotidiano",
    }
)
# Salud y Finanzas siguen sin lectura de agentes (RULES E.12; evaluación de privacidad F8-T03).
SENSITIVE_AREAS = frozenset({Area.SALUD, Area.FINANZAS})
SPECIALIST_AREAS = frozenset(
    {Area.UNIVERSIDAD, Area.PROYECTOS, Area.CARRERA, Area.HOBBIES, Area.COTIDIANO}
)
GOALS_NOTE = "metas.md"
PROPOSALS_ROOT = "Propuestas"


def area_folder(area: Area) -> str:
    return AREA_FOLDERS[area]


def goals_note(area: Area) -> str:
    return f"{AREA_FOLDERS[area]}/{GOALS_NOTE}"
