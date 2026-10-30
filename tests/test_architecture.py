"""ADR-0003: la dirección de dependencias entre capas se verifica en cada ejecución de pruebas."""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "src" / "lifeos"

# Capa -> capas de lifeos que puede importar.
ALLOWED = {
    "domain": {"domain"},
    "ports": {"domain", "ports"},
    "application": {"domain", "ports", "application"},
    "adapters": {"domain", "ports", "adapters"},
    # Presentación (ADR-0003, revisión 2026-10-28): traduce entrada/salida hacia los casos de uso
    # y obtiene sus dependencias de la raíz de composición.
    "cli": {"domain", "ports", "application", "cli", "bootstrap"},
}
# Dependencias de terceros permitidas por capa (ADR-0007). Dominio, puertos y aplicación no
# conocen PyYAML ni Pydantic: los adaptadores devuelven tipos propios ya validados.
THIRD_PARTY = {
    "domain": set(),
    "ports": set(),
    "application": set(),
    "adapters": {"yaml", "pydantic"},
    "cli": set(),
}
# Raíz de composición: único módulo que conecta capas (incluidos los adaptadores).
COMPOSITION_ROOT = "bootstrap.py"

pytestmark = pytest.mark.unit


def _lifeos_imports(path: Path) -> set[str]:
    layers: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        layers.update(name.split(".")[1] for name in names if name.startswith("lifeos."))
    return layers


@pytest.mark.parametrize("layer", sorted(ALLOWED))
def test_layer_only_imports_allowed_layers(layer: str) -> None:
    violations = {
        f"{path.relative_to(SRC)} -> {imported}"
        for path in (SRC / layer).rglob("*.py")
        for imported in _lifeos_imports(path) - ALLOWED[layer]
    }
    assert violations == set()


def _third_party_imports(path: Path) -> set[str]:
    stdlib_or_local = {*__import__("sys").stdlib_module_names, "lifeos"}
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            roots = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots = [node.module.split(".")[0]]
        else:
            continue
        found.update(root for root in roots if root not in stdlib_or_local)
    return found


@pytest.mark.parametrize("layer", sorted(THIRD_PARTY))
def test_layer_uses_only_allowed_third_party_packages(layer: str) -> None:
    violations = {
        f"{path.relative_to(SRC)} -> {package}"
        for path in (SRC / layer).rglob("*.py")
        for package in _third_party_imports(path) - THIRD_PARTY[layer]
    }
    assert violations == set()


def test_only_known_modules_live_at_package_root() -> None:
    # Un módulo suelto en lifeos/ escaparía a las comprobaciones por capa.
    loose = {p.name for p in SRC.glob("*.py")} - {"__init__.py", COMPOSITION_ROOT}
    assert loose == set()
    assert {p.name for p in SRC.iterdir() if p.is_dir() and p.name != "__pycache__"} <= {
        *ALLOWED,
    }


def test_no_module_imports_the_presentation_layer() -> None:
    importers = {
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if "cli" not in path.relative_to(SRC).parts and "cli" in _lifeos_imports(path)
    }
    assert importers == set()


def test_cli_does_not_import_planning_rules() -> None:
    # F3-T05: la CLI no contiene reglas de negocio ni llama directamente a las del planificador.
    rules = {"capacity", "plan_validator", "heuristic", "schedule", "horario_note"}
    imported = {
        node.module.rsplit(".", 1)[-1]
        for path in (SRC / "cli").rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported & rules == set()


def test_composition_root_has_no_third_party_imports() -> None:
    root = SRC / COMPOSITION_ROOT
    if root.exists():
        assert _third_party_imports(root) == set()


def test_source_uses_only_absolute_imports() -> None:
    # Los imports relativos esquivarían la comprobación de capas de arriba.
    relative = {
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level > 0
    }
    assert relative == set()
