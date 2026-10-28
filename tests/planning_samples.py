"""Horarios de prueba de la Fase 3. Datos inventados y aislados: nunca son límites reales del
propietario (D-13 los completa él en su bóveda)."""

HORARIO = """---
tipo: horario
zona: America/Lima
---

# Horario y límites

## Límites
- [foco_max_dia:: 4h] [libre_min_dia:: 1h] [margen:: 20%] [bloque_minimo:: 30m] [bloque_maximo:: 2h]

## Disponibilidad
- lun-vie 08:00–20:00
- sáb 09:00–13:00

## Compromisos fijos
- lun,mié 10:00–12:00 · Clase de redes [estado:: activa]
- vie 14:00–15:00 · Tutoría [estado:: sustituida]

## Tiempo protegido
- mar,jue 18:00–19:30 · Gym
- dom todo el día · Descanso

## No disponible
- 2026-11-06 08:00–20:00 · Viaje
"""

HORARIO_SIN_LIMITES = """---
tipo: horario
zona: America/Lima
---

## Límites
- [foco_max_dia:: ] [libre_min_dia:: ] [margen:: ]

## Disponibilidad
- lun-vie 08:00–20:00
"""


def horario(
    *,
    zona: str = "America/Lima",
    limites: str = "[foco_max_dia:: 4h] [libre_min_dia:: 1h] [margen:: 20%]",
    disponibilidad: str = "- lun-dom 08:00–20:00",
    fijos: str = "",
    protegido: str = "",
    no_disponible: str = "",
) -> str:
    return (
        f"---\ntipo: horario\nzona: {zona}\n---\n\n"
        f"## Límites\n- {limites}\n\n"
        f"## Disponibilidad\n{disponibilidad}\n\n"
        f"## Compromisos fijos\n{fijos}\n\n"
        f"## Tiempo protegido\n{protegido}\n\n"
        f"## No disponible\n{no_disponible}\n"
    )
