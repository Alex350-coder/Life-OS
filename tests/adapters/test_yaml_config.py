"""SEC-06: la configuración es dato, nunca código. Etiquetas, anclas y claves extra se rechazan."""

import copy
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from lifeos.adapters.yaml_config import (
    MAX_CONFIG_BYTES,
    YamlConfigSource,
    parse_agent,
    parse_catalog,
    parse_policy,
    strict_load,
)
from lifeos.ports.config import ConfigSyntaxError, WriteGrant, WriteMode
from lifeos.ports.vault import VaultPath
from tests import config_samples as samples

pytestmark = [pytest.mark.unit, pytest.mark.security]


# --- Cargador YAML estricto ---------------------------------------------------------------


def test_strict_load_accepts_plain_mappings_lists_and_scalars() -> None:
    assert strict_load("a: 1\nb: [x, 'y']\nc: {d: true}\n") == {
        "a": 1,
        "b": ["x", "y"],
        "c": {"d": True},
    }


@pytest.mark.parametrize(
    "text",
    [
        "a: !!python/object/apply:os.system ['echo hola']\n",
        "a: !!python/name:os.system\n",
        "a: !!str 1\n",
        "a: !!binary aGVsbG8=\n",
        "a: !personalizada x\n",
        "!!map {a: 1}\n",
        "a: !!seq [1]\n",
    ],
)
def test_strict_load_rejects_explicit_tags(text: str) -> None:
    with pytest.raises(ConfigSyntaxError, match="etiqueta"):
        strict_load(text)


@pytest.mark.parametrize(
    "text",
    [
        "a: &ancla 1\nb: *ancla\n",
        "base: &b {x: 1}\nhijo:\n  <<: *b\n",
        "a: &lista [1, 2]\n",
    ],
)
def test_strict_load_rejects_anchors_aliases_and_merges(text: str) -> None:
    with pytest.raises(ConfigSyntaxError, match=r"ancla|alias"):
        strict_load(text)


def test_strict_load_rejects_merge_key_without_anchor() -> None:
    with pytest.raises(ConfigSyntaxError, match="<<"):
        strict_load("hijo:\n  <<: {x: 1}\n")


def test_strict_load_rejects_duplicate_keys_at_any_depth() -> None:
    with pytest.raises(ConfigSyntaxError, match="duplicada"):
        strict_load("a: 1\na: 2\n")
    with pytest.raises(ConfigSyntaxError, match="duplicada"):
        strict_load("a:\n  escritura: []\n  escritura: [x]\n")


def test_strict_load_rejects_non_string_keys() -> None:
    with pytest.raises(ConfigSyntaxError, match="clave"):
        strict_load("1: a\n")


@pytest.mark.parametrize("text", ["a: 1\n---\nb: 2\n", "", "   \n", "# solo comentario\n"])
def test_strict_load_requires_exactly_one_document(text: str) -> None:
    with pytest.raises(ConfigSyntaxError):
        strict_load(text)


def test_strict_load_reports_yaml_syntax_errors() -> None:
    with pytest.raises(ConfigSyntaxError, match="YAML"):
        strict_load("a: [1, 2\n")


# --- Política ---------------------------------------------------------------------------


def test_parse_policy_converts_to_port_types() -> None:
    policy = parse_policy(samples.dump(samples.policy()).encode())
    assert VaultPath("_sistema") in policy.forbidden_write
    assert policy.restricted_read == (VaultPath("05-Salud"), VaultPath("06-Finanzas"))
    assert policy.plan_owner == "jefe"
    coach = policy.agents["coach"]
    assert coach.tools == frozenset(
        {"leer_nota", "listar_tareas", "crear_captura", "anexar_checkin"}
    )
    assert WriteGrant(VaultPath("Diario"), WriteMode.ANEXAR_SECCION, "check-ins") in coach.writes


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d.update(extra=1), "extra"),
        (lambda d: d["global"].update(nuevo=[]), "nuevo"),
        (lambda d: d["agentes"]["coach"].update(puede_todo=True), "puede_todo"),
        (lambda d: d["agentes"]["coach"]["escritura"][0].update(x=1), "x"),
        (lambda d: d.update(schema_version=2), "schema_version"),
        (lambda d: d["agentes"]["coach"]["escritura"][0].update(modo="borrar"), "modo"),
        (lambda d: d["agentes"]["coach"].update(lectura="Diario/"), "lectura"),
        (lambda d: d["agentes"]["coach"]["lectura"].append("../fuera/"), "lectura"),
        (lambda d: d["agentes"]["coach"]["lectura"].append("/"), "lectura"),
        (lambda d: d["agentes"]["coach"]["lectura"].append("C:/x/"), "lectura"),
        (
            lambda d: d["agentes"].update({"Coach Mayus": copy.deepcopy(d["agentes"]["coach"])}),
            "agentes",
        ),
    ],
)
def test_parse_policy_rejects_unknown_keys_and_invalid_values(
    mutate: object, fragment: str
) -> None:
    data = samples.policy()
    mutate(data)  # type: ignore[operator]
    with pytest.raises(ConfigSyntaxError) as info:
        parse_policy(samples.dump(data).encode())
    assert fragment in str(info.value)


def test_config_errors_do_not_echo_input_values() -> None:
    data = samples.policy()
    data["agentes"]["coach"]["escritura"][0]["modo"] = "valor-secreto-xyz"
    with pytest.raises(ConfigSyntaxError) as info:
        parse_policy(samples.dump(data).encode())
    assert "valor-secreto-xyz" not in str(info.value)


def test_parse_rejects_oversized_and_non_utf8_files() -> None:
    with pytest.raises(ConfigSyntaxError, match="tamaño"):
        parse_policy(b"#" * (MAX_CONFIG_BYTES + 1))
    with pytest.raises(ConfigSyntaxError, match="UTF-8"):
        parse_policy("a: ñ\n".encode("latin-1"))


# --- Catálogo de modelos ------------------------------------------------------------------


def test_parse_catalog_with_unknown_tariffs() -> None:
    catalog = parse_catalog(samples.dump(samples.catalog()).encode())
    assert catalog.providers["nvidia"].api_key_env == "NVIDIA_API_KEY"
    assert catalog.profiles["ligero"].timeout_s == 30
    assert catalog.tariff_date is None
    assert dict(catalog.tariffs) == {}


def test_parse_catalog_reads_tariffs_as_exact_decimals() -> None:
    data = samples.catalog()
    data["tarifas"] = {
        "fecha_tarifa": "2026-10-27",
        "modelos": {
            "proveedor/modelo-ligero": {
                "entrada_usd_por_millon": "0.10",
                "salida_usd_por_millon": "0.40",
            }
        },
    }
    catalog = parse_catalog(samples.dump(data).encode())
    tariff = catalog.tariffs["proveedor/modelo-ligero"]
    assert tariff.input_usd_per_million == Decimal("0.10")
    assert tariff.output_usd_per_million == Decimal("0.40")
    assert catalog.tariff_date == date(2026, 10, 27)


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d["perfiles"]["ligero"].update(proveedor="otro"), "proveedor"),
        (lambda d: d["proveedores"]["nvidia"].update(base_url="http://x.example/v1"), "https"),
        (lambda d: d["proveedores"]["nvidia"].update(api_key_env="nvapi-abc"), "api_key_env"),
        (lambda d: d["proveedores"]["nvidia"].update(api_key="valor"), "api_key"),
        (lambda d: d["perfiles"]["ligero"].update(timeout_s=0), "timeout_s"),
        (lambda d: d["perfiles"]["ligero"].update(timeout_s=1000), "timeout_s"),
        (lambda d: d["perfiles"]["ligero"].update(max_tokens="1024"), "max_tokens"),
        (
            lambda d: d["tarifas"].update(
                modelos={"m": {"entrada_usd_por_millon": 0.1, "salida_usd_por_millon": "1"}}
            ),
            "entrada_usd_por_millon",
        ),
        (
            lambda d: d["tarifas"].update(
                modelos={"m": {"entrada_usd_por_millon": "-1", "salida_usd_por_millon": "1"}}
            ),
            "entrada_usd_por_millon",
        ),
    ],
)
def test_parse_catalog_rejects_invalid_entries(mutate: object, fragment: str) -> None:
    data = samples.catalog()
    mutate(data)  # type: ignore[operator]
    with pytest.raises(ConfigSyntaxError) as info:
        parse_catalog(samples.dump(data).encode())
    assert fragment in str(info.value)


def test_tariffs_require_a_date() -> None:
    data = samples.catalog()
    data["tarifas"]["modelos"] = {
        "m": {"entrada_usd_por_millon": "1", "salida_usd_por_millon": "1"}
    }
    with pytest.raises(ConfigSyntaxError, match="fecha_tarifa"):
        parse_catalog(samples.dump(data).encode())


# --- Agentes ----------------------------------------------------------------------------


def test_parse_agent_records_hash_and_requested_permissions() -> None:
    raw = samples.dump(samples.coach()).encode()
    agent = parse_agent(raw)
    assert agent.id == "coach"
    assert agent.profile == "ligero"
    assert agent.reads == (VaultPath("_sistema"), VaultPath("Semanal"), VaultPath("Diario"))
    assert agent.writes[1] == WriteGrant(VaultPath("Diario"), WriteMode.ANEXAR_SECCION, "check-ins")
    assert len(agent.source_sha256) == 64


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda d: d.update(estado="activo"), "estado"),
        (lambda d: d.update(aprobado=True), "aprobado"),
        (lambda d: d.update(requiere_aprobacion=False), "requiere_aprobacion"),
        (lambda d: d.update(puede_modificar_plan_global=True), "puede_modificar_plan_global"),
        (lambda d: d.update(dueno_plan_global=True), "dueno_plan_global"),
        (
            lambda d: d["permisos_solicitados"]["escritura"].append(
                {"ruta": "Semanal/", "modo": "plan"}
            ),
            "modo",
        ),
        (lambda d: d["permisos_solicitados"].update(ejecutar=["x"]), "ejecutar"),
        (lambda d: d["modelo_solicitado"].update(modelo="otro"), "modelo"),
        (lambda d: d.update(id="Coach!"), "id"),
        (lambda d: d.update(version=0), "version"),
        (lambda d: d.update(version="1"), "version"),
        (lambda d: d["herramientas"].append("Shell Exec!"), "herramientas"),
        (lambda d: d["permisos_solicitados"]["escritura"][1].pop("seccion"), "seccion"),
        (lambda d: d["permisos_solicitados"]["escritura"][0].update(seccion="x"), "seccion"),
    ],
)
def test_parse_agent_rejects_privileges_and_unknown_keys(mutate: object, fragment: str) -> None:
    data = samples.coach()
    mutate(data)  # type: ignore[operator]
    with pytest.raises(ConfigSyntaxError) as info:
        parse_agent(samples.dump(data).encode())
    assert fragment in str(info.value)


def test_parse_agent_rejects_python_tag_payload() -> None:
    payload = samples.dump(samples.coach()) + "extra: !!python/object/apply:os.system ['x']\n"
    with pytest.raises(ConfigSyntaxError, match="etiqueta"):
        parse_agent(payload.encode())


# --- Fuente en disco ----------------------------------------------------------------------


def _write_config(root: Path) -> None:
    (root / "agents").mkdir(parents=True)
    (root / "permissions.yaml").write_text(samples.dump(samples.policy()), encoding="utf-8")
    (root / "models.yaml").write_text(samples.dump(samples.catalog()), encoding="utf-8")
    for agent in (samples.jefe(), samples.coach()):
        path = root / "agents" / f"{agent['id']}.yaml"
        path.write_text(samples.dump(agent), encoding="utf-8")


def test_yaml_config_source_loads_all_files(tmp_path: Path) -> None:
    _write_config(tmp_path)
    source = YamlConfigSource(tmp_path)
    assert source.load_policy().plan_owner == "jefe"
    assert set(source.load_catalog().profiles) == {"grande", "ligero"}
    assert [agent.id for agent in source.load_agents()] == ["coach", "jefe"]


def test_agent_file_name_must_match_its_id(tmp_path: Path) -> None:
    _write_config(tmp_path)
    (tmp_path / "agents" / "coach.yaml").rename(tmp_path / "agents" / "jefe2.yaml")
    with pytest.raises(ConfigSyntaxError, match="jefe2"):
        YamlConfigSource(tmp_path).load_agents()


def test_missing_config_file_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigSyntaxError, match=r"permissions.yaml"):
        YamlConfigSource(tmp_path).load_policy()
