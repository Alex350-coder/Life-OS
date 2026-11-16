"""Esquemas estrictos de las peticiones (frontera HTTP): claves desconocidas → 422.

Solo validan forma y tamaño. Los valores de dominio (estado, id de tarea, semana, avance) se
convierten con los mismos tipos del dominio que usa la CLI, y sus errores salen como 422.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

MAX_TEXT = 2_000
MAX_SHORT = 40
MAX_ANSWERS = 50


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CaptureIn(_Strict):
    texto: str = Field(min_length=1, max_length=MAX_TEXT)


class CheckInIn(_Strict):
    texto: str = Field(default="", max_length=MAX_TEXT)
    estado: str = Field(default="nota", max_length=MAX_SHORT)
    tarea: str | None = Field(default=None, max_length=MAX_SHORT)
    capturar: str | None = Field(default=None, max_length=MAX_TEXT)
    ia: StrictBool = False


class PlanIn(_Strict):
    semana: str | None = Field(default=None, max_length=MAX_SHORT)
    ia: StrictBool = False


class ApprovalIn(_Strict):
    semana: str | None = Field(default=None, max_length=MAX_SHORT)


class AnswerIn(_Strict):
    tarea: str = Field(max_length=MAX_SHORT)
    tipo: Literal["hecha", "parcial", "no"]
    avance: str | None = Field(default=None, max_length=MAX_SHORT)
    restante: str | None = Field(default=None, max_length=MAX_SHORT)


class ClosingIn(_Strict):
    respuestas: list[AnswerIn] = Field(default_factory=list, max_length=MAX_ANSWERS)


# --- Registro de agentes y propuestas (Fase 8) -----------------------------------------------

# Por debajo del límite del cuerpo (64 KiB, middleware): un agente real ocupa ~1 KiB.
MAX_AGENT_FILE = 32 * 1024


class AgentImportIn(BaseModel):
    # Sin recortar espacios: el archivo se guarda tal cual y su huella debe coincidir.
    model_config = ConfigDict(extra="forbid")

    nombre: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    contenido: str = Field(min_length=1, max_length=MAX_AGENT_FILE)


class AgentActionIn(_Strict):
    accion: Literal["aprobar", "activar", "desactivar", "restaurar"]
    version: int | None = Field(default=None, ge=1, le=1000)


class AgentRunIn(_Strict):
    semana: str | None = Field(default=None, max_length=MAX_SHORT)
    ia: StrictBool = False


class ProposalRefIn(_Strict):
    ref: str = Field(min_length=1, max_length=80)
