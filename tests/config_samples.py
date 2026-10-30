"""Configuraciones de prueba (no son las del propietario): política, catálogo y agentes mínimos.

Cada función devuelve un diccionario nuevo para que las pruebas puedan modificarlo sin
afectar a otras. `dump` lo serializa a YAML plano (sin etiquetas ni anclas).
"""

from collections.abc import Mapping
from typing import Any

import yaml

from lifeos.adapters.yaml_config import parse_agent, parse_catalog, parse_policy
from lifeos.application.permissions import (
    TOOL_WRITE_MODES,
    EffectivePermissions,
    resolve_effective,
)

KNOWN_CONTRACTS = frozenset({"captura_v1", "checkin_v1", "plan_v1", "coach_v1"})


def dump(data: object) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def policy() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "global": {
            "escritura_prohibida_para_agentes": ["_sistema/", "_logs/", "Archivo/"],
            "lectura_restringida": ["05-Salud/", "06-Finanzas/"],
            "dueno_plan_global": "jefe",
        },
        "agentes": {
            "jefe": {
                "herramientas": ["leer_nota", "listar_tareas", "crear_captura", "publicar_plan"],
                "lectura": ["_sistema/", "00-Inbox/", "01-Universidad/", "Semanal/", "Diario/"],
                "escritura": [
                    {"ruta": "Semanal/", "modo": "plan"},
                    {"ruta": "Diario/", "modo": "plan"},
                    {"ruta": "00-Inbox/", "modo": "crear"},
                ],
            },
            "coach": {
                "herramientas": ["leer_nota", "listar_tareas", "crear_captura", "anexar_checkin"],
                "lectura": ["_sistema/", "Semanal/", "Diario/"],
                "escritura": [
                    {"ruta": "00-Inbox/", "modo": "crear"},
                    {"ruta": "Diario/", "modo": "anexar_seccion", "seccion": "check-ins"},
                ],
            },
        },
    }


def catalog() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "proveedores": {
            "nvidia": {
                "base_url": "https://integrate.api.nvidia.com/v1",
                "api_key_env": "NVIDIA_API_KEY",
            }
        },
        "perfiles": {
            "grande": {
                "proveedor": "nvidia",
                "modelo": "proveedor/modelo-grande",
                "timeout_s": 60,
                "max_tokens": 2048,
            },
            "ligero": {
                "proveedor": "nvidia",
                "modelo": "proveedor/modelo-ligero",
                "timeout_s": 30,
                "max_tokens": 1024,
            },
        },
        "tarifas": {"fecha_tarifa": None, "modelos": {}},
    }


def coach() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "coach",
        "nombre": "Coach",
        "version": 1,
        "proposito": "Acompañar la ejecución del plan.",
        "modelo_solicitado": {"perfil": "ligero"},
        "contrato": {"entrada": "checkin_v1", "salida": "checkin_v1"},
        "herramientas": ["leer_nota", "listar_tareas", "crear_captura", "anexar_checkin"],
        "permisos_solicitados": {
            "lectura": ["_sistema/", "Semanal/", "Diario/"],
            "escritura": [
                {"ruta": "00-Inbox/", "modo": "crear"},
                {"ruta": "Diario/", "modo": "anexar_seccion", "seccion": "check-ins"},
            ],
        },
        "metricas": ["checkins_registrados"],
    }


def jefe() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": "jefe",
        "nombre": "Jefe",
        "version": 1,
        "proposito": "Consolidar el plan.",
        "modelo_solicitado": {"perfil": "grande"},
        "contrato": {"entrada": "captura_v1", "salida": "captura_v1"},
        "herramientas": ["leer_nota", "listar_tareas", "crear_captura", "publicar_plan"],
        "permisos_solicitados": {
            "lectura": ["_sistema/", "00-Inbox/", "01-Universidad/", "Semanal/", "Diario/"],
            "escritura": [{"ruta": "00-Inbox/", "modo": "crear"}],
        },
        "metricas": [],
    }


def effective() -> Mapping[str, EffectivePermissions]:
    """Permisos efectivos de Jefe y Coach con la política y el catálogo de prueba."""
    return resolve_effective(
        parse_policy(dump(policy()).encode()),
        parse_catalog(dump(catalog()).encode()),
        (parse_agent(dump(jefe()).encode()), parse_agent(dump(coach()).encode())),
        tools=TOOL_WRITE_MODES,
        contracts=KNOWN_CONTRACTS,
    )
