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
}

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


def test_runtime_has_no_third_party_dependencies() -> None:
    stdlib_or_local = {*__import__("sys").stdlib_module_names, "lifeos"}
    third_party: set[str] = set()
    for path in SRC.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                roots = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots = [node.module.split(".")[0]]
            else:
                continue
            third_party.update(root for root in roots if root not in stdlib_or_local)
    assert third_party == set()
