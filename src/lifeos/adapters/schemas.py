"""Esquemas de salida del modelo (SEC-07): JSON estricto → objeto de dominio, o rechazo.

Se tolera como máximo una envoltura ```json … ``` alrededor del objeto; cualquier texto extra,
clave desconocida o duplicada, tipo incorrecto o constante no estándar (NaN) se rechaza. Los
mensajes de error identifican el campo y el problema, nunca reproducen el texto del modelo.
"""

import json
import re
from collections.abc import Callable, Mapping
from datetime import time
from types import MappingProxyType
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from lifeos.domain.checkin import MAX_CHECKIN_CHARS, CheckInEntry
from lifeos.domain.errors import ValidationError as DomainValidationError
from lifeos.domain.proposals import MAX_DRAFT_CHARS, CaptureDraft
from lifeos.ports.llm import ModelOutputError, OutputSchema

MAX_OUTPUT_CHARS = 64 * 1024
_FENCE_RE = re.compile(r"^```(?:json)?[ \t]*\r?\n(.*)\r?\n```$", re.DOTALL)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _CaptureV1(_Strict):
    texto: Annotated[str, StringConstraints(min_length=1, max_length=MAX_DRAFT_CHARS)]


class _CheckinV1(_Strict):
    hora: Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]
    texto: Annotated[str, StringConstraints(min_length=1, max_length=MAX_CHECKIN_CHARS)]


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("clave duplicada")
        result[key] = value
    return result


def _reject_constant(name: str) -> object:
    raise ValueError("constante JSON no estándar")


def _unwrap(raw: str) -> str:
    text = raw.strip()
    match = _FENCE_RE.match(text)
    return match[1].strip() if match else text


class PydanticOutputSchema[M: BaseModel, T]:
    def __init__(self, schema_id: str, model: type[M], convert: Callable[[M], T]) -> None:
        self._id = schema_id
        self._model = model
        self._convert = convert

    @property
    def id(self) -> str:
        return self._id

    def _fail(self, reason: str) -> ModelOutputError:
        return ModelOutputError(f"salida no válida para {self._id}: {reason}")

    def parse(self, raw: str) -> T:
        if len(raw) > MAX_OUTPUT_CHARS:
            raise self._fail(f"supera el tamaño máximo de {MAX_OUTPUT_CHARS} caracteres")
        try:
            data = json.loads(
                _unwrap(raw), object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant
            )
        except RecursionError:
            raise self._fail("JSON anidado en exceso") from None
        except ValueError as error:
            reason = str(error) if not isinstance(error, json.JSONDecodeError) else "no es JSON"
            raise self._fail(reason) from None
        if not isinstance(data, dict):
            raise self._fail("se esperaba un objeto JSON")
        try:
            return self._convert(self._model.model_validate(data))
        except ValidationError as error:
            fields = sorted(
                {
                    f"{'.'.join(str(p) for p in item['loc']) or '(raíz)'} ({item['type']})"
                    for item in error.errors(include_input=False, include_url=False)
                }
            )
            raise self._fail(", ".join(fields)) from None
        except DomainValidationError as error:
            raise self._fail(f"{error.field}: {error.reason}") from None


def _to_checkin(parsed: _CheckinV1) -> CheckInEntry:
    hours, minutes = parsed.hora.split(":")
    return CheckInEntry(time(int(hours), int(minutes)), parsed.texto)


SCHEMAS: Mapping[str, OutputSchema[Any]] = MappingProxyType(
    {
        "captura_v1": PydanticOutputSchema(
            "captura_v1", _CaptureV1, lambda parsed: CaptureDraft(parsed.texto)
        ),
        "checkin_v1": PydanticOutputSchema("checkin_v1", _CheckinV1, _to_checkin),
    }
)
