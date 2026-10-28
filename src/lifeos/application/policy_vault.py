"""Bóveda con permisos de un agente (SEC-04): envuelve un `VaultStore` y aplica en código lo
que la política concede. El prompt nunca es la barrera.

- Lectura, existencia y listado: solo dentro de las raíces de lectura efectivas.
- `CREAR`: solo notas nuevas (`expected is None`) bajo una raíz con permiso de creación.
- `ANEXAR_SECCION`: solo notas existentes, y el texto nuevo debe ser el anterior más líneas
  insertadas dentro de la sección concedida.
- `PLAN` (SEC-09): solo las concesiones `plan` del dueño del plan global, solo notas con nombre
  canónico (`Semanal/AAAA-Wnn.md`, `Diario/AAAA-MM-DD.md`) y, al republicar un diario, sin
  alterar las secciones del Coach y del propietario (`Check-ins`, `Cierre`). Que el contenido
  sea un plan validado lo garantiza la herramienta `publicar_plan`.
"""

import re

from lifeos.application.permissions import EffectivePermissions
from lifeos.application.tasks import NOTE_SUFFIX, ApplicationError
from lifeos.domain.sections import only_inserted_in_section, section_lines
from lifeos.ports.config import WriteGrant, WriteMode
from lifeos.ports.vault import (
    Snapshot,
    VaultPath,
    VaultStore,
    Version,
    VersionConflictError,
    within_any,
)

WEEKLY_ROOT = VaultPath("Semanal")
DAILY_ROOT = VaultPath("Diario")
PLAN_NOTE_NAMES = (
    (WEEKLY_ROOT, re.compile(r"\d{4}-W\d{2}\.md")),
    (DAILY_ROOT, re.compile(r"\d{4}-\d{2}-\d{2}\.md")),
)
# Secciones de la nota diaria que el plan nunca reescribe (Coach y propietario).
PROTECTED_DAILY_SECTIONS = ("check-ins", "cierre")


class PermissionDeniedError(ApplicationError):
    """La operación excede los permisos efectivos del agente. `reason` es un código estable."""

    def __init__(self, agent_id: str, reason: str, target: str) -> None:
        super().__init__(f"{agent_id}: operación denegada ({reason}) sobre {target}")
        self.agent_id = agent_id
        self.reason = reason
        self.target = target


class PolicyVault:
    """Implementación de `VaultStore` restringida a un agente y a un modo de escritura."""

    def __init__(
        self, inner: VaultStore, permissions: EffectivePermissions, mode: WriteMode | None
    ) -> None:
        self._inner = inner
        self._perms = permissions
        self._mode = mode

    def _deny(self, reason: str, path: VaultPath | None) -> PermissionDeniedError:
        return PermissionDeniedError(self._perms.agent_id, reason, str(path or "(bóveda)"))

    def _readable(self, path: VaultPath) -> bool:
        return within_any(path, self._perms.reads)

    def _require_readable(self, path: VaultPath) -> None:
        if not self._readable(path):
            raise self._deny("lectura_no_autorizada", path)

    # --- Lectura ---------------------------------------------------------------------------

    def read(self, path: VaultPath) -> Snapshot:
        self._require_readable(path)
        return self._inner.read(path)

    def exists(self, path: VaultPath) -> bool:
        self._require_readable(path)
        return self._inner.exists(path)

    def list_notes(self, prefix: VaultPath | None = None) -> tuple[VaultPath, ...]:
        if prefix is not None:
            self._require_readable(prefix)
        return tuple(path for path in self._inner.list_notes(prefix) if self._readable(path))

    # --- Escritura -------------------------------------------------------------------------

    def _check_target(self, path: VaultPath) -> None:
        # Defensa en profundidad: aunque una concesión lo cubriera, estas raíces nunca se escriben.
        if within_any(path, self._perms.forbidden_write):
            raise self._deny("carpeta_prohibida", path)
        if not path.name.lower().endswith(NOTE_SUFFIX):
            raise self._deny("no_es_nota", path)

    def _write_plan(self, path: VaultPath, text: str, expected: Version | None) -> Version:
        self._check_target(path)
        if not any(within_any(path, (grant.root,)) for grant in self._perms.plan_writes):
            raise self._deny("escritura_no_autorizada", path)
        if not any(
            path.parent == root and pattern.fullmatch(path.name)
            for root, pattern in PLAN_NOTE_NAMES
        ):
            raise self._deny("nombre_de_plan_invalido", path)
        if expected is not None and within_any(path, (DAILY_ROOT,)):
            current = self._inner.read(path)
            if current.version != expected:
                raise VersionConflictError(f"{path} cambió desde la última lectura")
            for heading in PROTECTED_DAILY_SECTIONS:
                old = section_lines(current.text, heading)
                if old is not None and old != section_lines(text, heading):
                    raise self._deny("seccion_protegida_alterada", path)
        return self._inner.write(path, text, expected=expected)

    def _grants_for(self, path: VaultPath) -> tuple[WriteGrant, ...]:
        if self._mode is None:
            raise self._deny("solo_lectura", path)
        self._check_target(path)
        grants = tuple(
            grant
            for grant in self._perms.writes
            if grant.mode is self._mode and within_any(path, (grant.root,))
        )
        if not grants:
            raise self._deny("escritura_no_autorizada", path)
        return grants

    def write(self, path: VaultPath, text: str, *, expected: Version | None) -> Version:
        if self._mode is WriteMode.PLAN:
            return self._write_plan(path, text, expected)
        grants = self._grants_for(path)
        if self._mode is WriteMode.CREAR:
            if expected is not None:
                raise self._deny("crear_no_edita", path)
            return self._inner.write(path, text, expected=None)
        if expected is None:
            raise self._deny("anexar_requiere_nota", path)
        current = self._inner.read(path)
        if current.version != expected:
            raise VersionConflictError(f"{path} cambió desde la última lectura")
        if not any(
            grant.section is not None
            and only_inserted_in_section(current.text, text, grant.section)
            for grant in grants
        ):
            raise self._deny("fuera_de_seccion", path)
        return self._inner.write(path, text, expected=expected)
