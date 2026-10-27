"""Bóveda con permisos de un agente (SEC-04): envuelve un `VaultStore` y aplica en código lo
que la política concede. El prompt nunca es la barrera.

- Lectura, existencia y listado: solo dentro de las raíces de lectura efectivas.
- `CREAR`: solo notas nuevas (`expected is None`) bajo una raíz con permiso de creación.
- `ANEXAR_SECCION`: solo notas existentes, y el texto nuevo debe ser el anterior más líneas
  insertadas dentro de la sección concedida.
- `PLAN`: no disponible hasta la Fase 3 (publicación de un plan validado por el Jefe).
"""

from lifeos.application.permissions import EffectivePermissions
from lifeos.application.tasks import NOTE_SUFFIX, ApplicationError
from lifeos.domain.sections import only_inserted_in_section
from lifeos.ports.config import WriteGrant, WriteMode
from lifeos.ports.vault import (
    Snapshot,
    VaultPath,
    VaultStore,
    Version,
    VersionConflictError,
    within_any,
)


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

    def _grants_for(self, path: VaultPath) -> tuple[WriteGrant, ...]:
        if self._mode is None:
            raise self._deny("solo_lectura", path)
        if self._mode is WriteMode.PLAN:
            raise self._deny("plan_no_disponible", path)
        # Defensa en profundidad: aunque una concesión lo cubriera, estas raíces nunca se escriben.
        if within_any(path, self._perms.forbidden_write):
            raise self._deny("carpeta_prohibida", path)
        if not path.name.lower().endswith(NOTE_SUFFIX):
            raise self._deny("no_es_nota", path)
        grants = tuple(
            grant
            for grant in self._perms.writes
            if grant.mode is self._mode and within_any(path, (grant.root,))
        )
        if not grants:
            raise self._deny("escritura_no_autorizada", path)
        return grants

    def write(self, path: VaultPath, text: str, *, expected: Version | None) -> Version:
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
