from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.routers.common import resolve_month
from app.services import metrics
from app.services import access

router = APIRouter(prefix="/api/metrics", tags=["métricas"])


@router.get(
    "/monthly",
    summary="Indicadores mensais",
    description="Matriculados, ativos, novas matrículas, desistências, aulas, presença e "
    "frequência individual. Sem year/month usa o mês atual. Cálculo determinístico.",
)
def get_monthly_metrics(
    year: int | None = None,
    month: int | None = None,
    workshop_id: int | None = None,
    class_id: int | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if workshop_id is not None:
        access.ensure_workshop_access(db, current_user, workshop_id)
    if class_id is not None:
        access.ensure_class_access(db, current_user, class_id)
    year, month = resolve_month(year, month)
    return metrics.get_monthly_metrics(
        db, year, month, workshop_id, class_id,
        access.professor_instructor_id(current_user),
        organization_id=current_user.organization_id,
    )
