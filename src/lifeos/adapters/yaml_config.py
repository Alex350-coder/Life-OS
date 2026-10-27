"""Carga estricta de la configuración YAML (SEC-06) y conversión a tipos de `ports.config`.

La configuración es dato, nunca código. Antes de construir nada se recorren los eventos del
analizador y se rechazan etiquetas explícitas (`!!python/...`, `!!str`, `!x`), anclas, alias,
claves de fusión `<<` y documentos múltiples. Al construir se rechazan claves duplicadas o no
textuales. Después, Pydantic valida el esquema con `extra="forbid"`; sus mensajes se generan
sin reproducir los valores recibidos.
"""

import hashlib
import re
from collections.abc import Callable, Hashable, Mapping
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

from lifeos.ports.config import (
    AgentCeiling,
    AgentConfig,
    ConfigSyntaxError,
    ModelCatalog,
    Policy,
    Profile,
    ProviderConfig,
    Tariff,
    WriteGrant,
    WriteMode,
)
from lifeos.ports.vault import PathRejectedError, VaultPath

MAX_CONFIG_BYTES = 64 * 1024
POLICY_FILE = "permissions.yaml"
CATALOG_FILE = "models.yaml"
AGENTS_DIR = "agents"
AGENT_SUFFIX = ".yaml"
_MERGE_KEY = "<<"
_BOM = "﻿"

# --- Cargador estricto ------------------------------------------------------------------------


def _mark(event_or_node: Any) -> str:
    mark = getattr(event_or_node, "start_mark", None)
    return f" (línea {mark.line + 1})" if mark is not None else ""


def _is_merge_key(event: yaml.Event) -> bool:
    return isinstance(event, yaml.ScalarEvent) and event.value == _MERGE_KEY and not event.style


def _reject_unsafe_events(text: str, source: str) -> None:
    documents = 0
    for event in yaml.parse(text, Loader=yaml.SafeLoader):
        if isinstance(event, yaml.DocumentStartEvent):
            documents += 1
        elif isinstance(event, yaml.AliasEvent):
            raise ConfigSyntaxError(source, (f"alias no permitido{_mark(event)}",))
        elif isinstance(event, yaml.ScalarEvent | yaml.CollectionStartEvent):
            if event.anchor is not None:
                raise ConfigSyntaxError(source, (f"ancla no permitida{_mark(event)}",))
            if event.tag is not None:
                raise ConfigSyntaxError(source, (f"etiqueta YAML no permitida{_mark(event)}",))
            if (
                isinstance(event, yaml.ScalarEvent)
                and event.value == _MERGE_KEY
                and not event.style
            ):
                raise ConfigSyntaxError(
                    source, (f"clave de fusión '<<' no permitida{_mark(event)}",)
                )
    if documents != 1:
        raise ConfigSyntaxError(source, (f"se esperaba un único documento, hay {documents}",))


class _StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Hashable, Any]:
        seen: set[str] = set()
        for key_node, _value in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise yaml.constructor.ConstructorError(
                    None, None, f"clave no textual{_mark(key_node)}", key_node.start_mark
                )
            if key in seen:
                raise yaml.constructor.ConstructorError(
                    None, None, f"clave duplicada {key!r}{_mark(key_node)}", key_node.start_mark
                )
            seen.add(key)
        return super().construct_mapping(node, deep=deep)


def strict_load(text: str, source: str = "config") -> object:
    """Devuelve el único documento como dict/list/escalares, o lanza `ConfigSyntaxError`."""
    try:
        _reject_unsafe_events(text, source)
        loader = _StrictLoader(text)
        try:
            return loader.get_single_data()
        finally:
            loader.dispose()
    except yaml.YAMLError as error:
        # Solo el problema y la línea: el contexto de PyYAML reproduce fragmentos del archivo.
        problem = getattr(error, "problem", None) or type(error).__name__
        raise ConfigSyntaxError(source, (f"YAML no válido: {problem}{_mark_of(error)}",)) from None


def _mark_of(error: yaml.YAMLError) -> str:
    mark = getattr(error, "problem_mark", None)
    return f" (línea {mark.line + 1})" if mark is not None else ""


def _decode(data: bytes, source: str) -> str:
    if len(data) > MAX_CONFIG_BYTES:
        raise ConfigSyntaxError(source, (f"supera el tamaño máximo de {MAX_CONFIG_BYTES} bytes",))
    try:
        return data.decode("utf-8").removeprefix(_BOM)
    except UnicodeDecodeError:
        raise ConfigSyntaxError(source, ("el archivo no está codificado en UTF-8",)) from None


# --- Esquemas (Pydantic, solo dentro de este adaptador) ---------------------------------------


def _check_root(raw: str) -> str:
    """Raíz de la bóveda escrita como `Carpeta/` o `Carpeta/Sub/`; nunca vacía ni absoluta."""
    try:
        return VaultPath(raw.removesuffix("/")).value
    except PathRejectedError as error:
        raise ValueError(f"ruta no válida: {error.reason}") from None


def _check_https(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError("base_url debe usar https")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("base_url no puede llevar credenciales, consulta ni fragmento")
    return url.rstrip("/")


Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,32}$")]
ToolName = Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,40}$")]
Section = Annotated[str, StringConstraints(pattern=r"^[a-z0-9-]{1,40}$")]
Root = Annotated[str, StringConstraints(max_length=256), AfterValidator(_check_root)]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,63}$")]
ModelName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._/:-]{1,128}$")]
UsdAmount = Annotated[str, StringConstraints(pattern=r"^\d{1,6}(\.\d{1,6})?$")]
SchemaId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]{1,40}$")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _PolicyWrite(_Strict):
    ruta: Root
    modo: Literal["crear", "anexar_seccion", "plan"]
    seccion: Section | None = None


class _Ceiling(_Strict):
    herramientas: list[ToolName]
    lectura: list[Root]
    escritura: list[_PolicyWrite]


class _Global(_Strict):
    escritura_prohibida_para_agentes: list[Root]
    lectura_restringida: list[Root]
    dueno_plan_global: Identifier


class _PolicyFile(_Strict):
    schema_version: Literal[1]
    global_: _Global = Field(alias="global")
    agentes: dict[Identifier, _Ceiling]


class _Provider(_Strict):
    base_url: Annotated[str, AfterValidator(_check_https)]
    api_key_env: EnvName


class _Profile(_Strict):
    proveedor: Identifier
    modelo: ModelName
    timeout_s: float = Field(gt=0, le=300)
    max_tokens: int = Field(ge=1, le=32_768)


class _TariffEntry(_Strict):
    entrada_usd_por_millon: UsdAmount
    salida_usd_por_millon: UsdAmount


class _Tariffs(_Strict):
    # Lax solo aquí: YAML entrega la fecha como `date` o como texto entrecomillado.
    fecha_tarifa: Annotated[date | None, Field(strict=False)]
    modelos: dict[ModelName, _TariffEntry]


class _CatalogFile(_Strict):
    schema_version: Literal[1]
    proveedores: dict[Identifier, _Provider] = Field(min_length=1)
    perfiles: dict[Identifier, _Profile] = Field(min_length=1)
    tarifas: _Tariffs

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        for name, profile in self.perfiles.items():
            if profile.proveedor not in self.proveedores:
                raise ValueError(f"perfil '{name}': proveedor no declarado")
        if self.tarifas.modelos and self.tarifas.fecha_tarifa is None:
            raise ValueError("fecha_tarifa es obligatoria si hay tarifas")
        return self


class _AgentWrite(_Strict):
    ruta: Root
    modo: Literal["crear", "anexar_seccion"]  # `plan` no se solicita: lo concede la política
    seccion: Section | None = None

    @model_validator(mode="after")
    def _section_matches_mode(self) -> Self:
        if (self.modo == "anexar_seccion") != (self.seccion is not None):
            raise ValueError("'seccion' es obligatoria con anexar_seccion y solo con ese modo")
        return self


class _RequestedModel(_Strict):
    perfil: Identifier


class _Contract(_Strict):
    entrada: SchemaId
    salida: SchemaId


class _Requested(_Strict):
    lectura: list[Root]
    escritura: list[_AgentWrite]


class _AgentFile(_Strict):
    schema_version: Literal[1]
    id: Identifier
    nombre: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    version: int = Field(ge=1)
    proposito: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    modelo_solicitado: _RequestedModel
    contrato: _Contract
    herramientas: list[ToolName]
    permisos_solicitados: _Requested
    metricas: list[SchemaId]


def _validate[M: _Strict](model: type[M], data: bytes, source: str) -> M:
    loaded = strict_load(_decode(data, source), source)
    if not isinstance(loaded, dict):
        raise ConfigSyntaxError(source, ("la raíz del archivo debe ser un mapa",))
    try:
        return model.model_validate(loaded)
    except ValidationError as error:
        raise ConfigSyntaxError(source, _describe(error)) from None


def _describe(error: ValidationError) -> tuple[str, ...]:
    # Sin `input` ni `url`: el mensaje identifica el campo, no reproduce su valor.
    return tuple(
        f"{'.'.join(str(part) for part in item['loc']) or '(raíz)'}: {item['msg']}"
        for item in error.errors(include_input=False, include_url=False)
    )


# --- Conversión a tipos de puertos ------------------------------------------------------------


def _roots(raw: list[str]) -> tuple[VaultPath, ...]:
    return tuple(VaultPath(value) for value in raw)


def _grant(ruta: str, modo: str, seccion: str | None) -> WriteGrant:
    return WriteGrant(VaultPath(ruta), WriteMode(modo), seccion)


def _frozen[K, V](items: Mapping[K, V]) -> Mapping[K, V]:
    return MappingProxyType(dict(items))


def parse_policy(data: bytes, source: str = POLICY_FILE) -> Policy:
    parsed = _validate(_PolicyFile, data, source)
    agents = {
        name: AgentCeiling(
            reads=_roots(ceiling.lectura),
            writes=tuple(_grant(w.ruta, w.modo, w.seccion) for w in ceiling.escritura),
            tools=frozenset(ceiling.herramientas),
        )
        for name, ceiling in parsed.agentes.items()
    }
    return Policy(
        forbidden_write=_roots(parsed.global_.escritura_prohibida_para_agentes),
        restricted_read=_roots(parsed.global_.lectura_restringida),
        plan_owner=parsed.global_.dueno_plan_global,
        agents=_frozen(agents),
    )


def parse_catalog(data: bytes, source: str = CATALOG_FILE) -> ModelCatalog:
    parsed = _validate(_CatalogFile, data, source)
    return ModelCatalog(
        providers=_frozen(
            {n: ProviderConfig(n, p.base_url, p.api_key_env) for n, p in parsed.proveedores.items()}
        ),
        profiles=_frozen(
            {
                n: Profile(n, p.proveedor, p.modelo, p.timeout_s, p.max_tokens)
                for n, p in parsed.perfiles.items()
            }
        ),
        tariff_date=parsed.tarifas.fecha_tarifa,
        tariffs=_frozen(
            {
                model: Tariff(
                    Decimal(entry.entrada_usd_por_millon), Decimal(entry.salida_usd_por_millon)
                )
                for model, entry in parsed.tarifas.modelos.items()
            }
        ),
    )


def parse_agent(data: bytes, source: str = "agente") -> AgentConfig:
    parsed = _validate(_AgentFile, data, source)
    requested = parsed.permisos_solicitados
    return AgentConfig(
        id=parsed.id,
        name=parsed.nombre,
        version=parsed.version,
        purpose=parsed.proposito,
        profile=parsed.modelo_solicitado.perfil,
        contract_in=parsed.contrato.entrada,
        contract_out=parsed.contrato.salida,
        tools=tuple(parsed.herramientas),
        reads=_roots(requested.lectura),
        writes=tuple(_grant(w.ruta, w.modo, w.seccion) for w in requested.escritura),
        metrics=tuple(parsed.metricas),
        source_sha256=hashlib.sha256(data).hexdigest(),
    )


# --- Fuente en disco --------------------------------------------------------------------------

_AGENT_NAME = re.compile(r"[a-z0-9-]{1,32}")


class YamlConfigSource:
    """Lee `permissions.yaml`, `models.yaml` y `agents/<id>.yaml` de una carpeta del proyecto."""

    def __init__(self, config_dir: Path) -> None:
        self._dir = config_dir

    def _load[T](self, relative: str, parse: Callable[[bytes, str], T]) -> T:
        path = self._dir / relative
        try:
            data = path.read_bytes()
        except FileNotFoundError:
            raise ConfigSyntaxError(relative, ("archivo no encontrado",)) from None
        except OSError as error:
            raise ConfigSyntaxError(relative, (f"no se pudo leer: {error.strerror}",)) from None
        return parse(data, relative)

    def load_policy(self) -> Policy:
        return self._load(POLICY_FILE, parse_policy)

    def load_catalog(self) -> ModelCatalog:
        return self._load(CATALOG_FILE, parse_catalog)

    def load_agents(self) -> tuple[AgentConfig, ...]:
        agents_dir = self._dir / AGENTS_DIR
        if not agents_dir.is_dir():
            raise ConfigSyntaxError(AGENTS_DIR, ("carpeta no encontrada",))
        agents = []
        for path in sorted(agents_dir.glob(f"*{AGENT_SUFFIX}")):
            relative = f"{AGENTS_DIR}/{path.name}"
            agent = self._load(relative, parse_agent)
            if not _AGENT_NAME.fullmatch(path.stem) or agent.id != path.stem:
                raise ConfigSyntaxError(relative, ("el id no coincide con el nombre del archivo",))
            agents.append(agent)
        return tuple(agents)
