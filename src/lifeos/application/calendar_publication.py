"""Publicación del plan aprobado en Google Calendar (F7-T03, ADR-0017, RULES G.4–G.5).

1. Lee el plan vigente de la semana (el último de la cadena, ADR-0011). Si no está aprobado,
   no se publica nada.
2. Calcula qué crear, actualizar y borrar comparando claves con lo ya publicado (sin duplicados).
3. `preview` solo muestra ese cambio; `publish` lo aplica como el Jefe, con la herramienta
   `publicar_calendario` y auditoría por operación. La presentación pide la confirmación
   explícita del propietario antes de llamar a `publish`.
"""

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from lifeos.application.calendar_tools import AppliedSync, CalendarTools
from lifeos.application.jefe import JefePlanner
from lifeos.application.tasks import ApplicationError
from lifeos.application.tools import ActorContext
from lifeos.domain.calendar_sync import SyncPlan, desired_entries, plan_sync
from lifeos.domain.capacity import local_datetime
from lifeos.domain.plan import PlanStatus
from lifeos.domain.schedule import MINUTES_PER_DAY
from lifeos.domain.weeks import IsoWeek


class CalendarNotApprovedError(ApplicationError):
    """La semana no tiene un plan aprobado: no se publica nada (RULES G.5)."""


@dataclass(frozen=True, slots=True)
class CalendarPreview:
    week: IsoWeek
    zone: ZoneInfo
    path: str  # nota del plan aprobado que se publica
    sync: SyncPlan


@dataclass(frozen=True, slots=True)
class CalendarResult:
    preview: CalendarPreview
    applied: AppliedSync


class CalendarPublisher:
    def __init__(self, jefe: JefePlanner, calendar: CalendarTools) -> None:
        self._jefe = jefe
        self._calendar = calendar

    def _prepare(self, week: IsoWeek | None) -> tuple[ActorContext, CalendarPreview]:
        view = self._jefe.semana(week)
        if view.path is None:
            raise CalendarNotApprovedError(f"no hay plan para {view.week}: nada que publicar")
        if view.status is not PlanStatus.APROBADO:
            raise CalendarNotApprovedError(
                f"el plan vigente de {view.week} ({view.path}) no está aprobado: usa primero "
                "'lifeos aprobar'"
            )
        actor = self._jefe.new_actor()
        start, end = _week_range(view.week, view.zone)
        label = view.week.render()
        published = self._calendar.published(actor, label, start, end)
        sync = plan_sync(desired_entries(view.week, view.blocks), published)
        return actor, CalendarPreview(view.week, view.zone, view.path, sync)

    def preview(self, week: IsoWeek | None = None) -> CalendarPreview:
        """Lo que haría `publish`, sin escribir nada."""
        return self._prepare(week)[1]

    def publish(self, week: IsoWeek | None = None) -> CalendarResult:
        """Aplica el cambio recalculado en este momento (lo publicado pudo cambiar)."""
        actor, preview = self._prepare(week)
        if preview.sync.is_empty:
            return CalendarResult(preview, AppliedSync(0, 0, 0))
        applied = self._calendar.apply(actor, preview.week.render(), preview.sync)
        return CalendarResult(preview, applied)


def _week_range(week: IsoWeek, zone: ZoneInfo) -> tuple[datetime, datetime]:
    days = week.days()
    return local_datetime(days[0], 0, zone), local_datetime(days[-1], MINUTES_PER_DAY, zone)
