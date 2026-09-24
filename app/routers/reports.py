from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.routers.common import resolve_month
from app.schemas import ReportGenerateIn, ReportUpdate, SuggestionIn
from app.services import export, reports
from app.services import access

router = APIRouter(prefix="/api/reports", tags=["relatórios"])


@router.get("", summary="Listar relatórios")
def list_reports(
    workshop_id: int | None = None,
    year: int | None = None,
    status: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if workshop_id is not None:
        access.ensure_workshop_access(db, current_user, workshop_id)
    return reports.list_reports(
        db, workshop_id, year, status, access.professor_instructor_id(current_user),
        organization_id=current_user.organization_id,
    )


@router.get(
    "/monthly",
    summary="Prévia dos dados do relatório mensal",
    description="Identificação, métricas e registros do mês de uma oficina, sem criar relatório.",
)
def preview_monthly_report(
    workshop_id: int, year: int | None = None, month: int | None = None,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    access.ensure_workshop_access(db, current_user, workshop_id)
    year, month = resolve_month(year, month)
    return reports.build_monthly_report_data(
        db, workshop_id, year, month, organization_id=current_user.organization_id
    )


@router.post(
    "/generate",
    summary="Gerar rascunho do relatório mensal",
    description="Cria o rascunho (ou devolve o existente sem sobrescrever edições). "
    "use_ai=true pede à IA a consolidação dos textos, se configurada.",
)
def generate_monthly_report(payload: ReportGenerateIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_workshop_access(db, current_user, payload.workshop_id)
    return reports.generate_monthly_report(
        db, payload.workshop_id, payload.year, payload.month, payload.use_ai,
        organization_id=current_user.organization_id,
    )


@router.get("/{report_id}", summary="Detalhar relatório")
def get_report(report_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_report_access(db, current_user, report_id)
    return reports.get_report(db, report_id, organization_id=current_user.organization_id)


@router.patch("/{report_id}", summary="Editar campos do relatório (rascunho)")
def update_report(report_id: int, payload: ReportUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_report_access(db, current_user, report_id)
    return reports.update_report(
        db, report_id, payload.model_dump(exclude_unset=True), organization_id=current_user.organization_id
    )


@router.post(
    "/{report_id}/suggestions",
    summary="Sugerir texto para um campo",
    description="Retorna uma sugestão (não salva) a partir dos registros das aulas.",
)
def suggest_report_text(report_id: int, payload: SuggestionIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_report_access(db, current_user, report_id)
    return reports.suggest_report_text(
        db, report_id, payload.field, payload.use_ai, organization_id=current_user.organization_id
    )


@router.post("/{report_id}/finalize", summary="Finalizar relatório")
def finalize_report(report_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_report_access(db, current_user, report_id)
    return reports.finalize_report(db, report_id, organization_id=current_user.organization_id)


@router.post("/{report_id}/reopen", summary="Reabrir relatório finalizado")
def reopen_report(report_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_report_access(db, current_user, report_id)
    return reports.reopen_report(db, report_id, organization_id=current_user.organization_id)


@router.get("/{report_id}/export.docx", summary="Exportar relatório em DOCX")
def export_report_docx(report_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_report_access(db, current_user, report_id)
    data = reports.get_report(db, report_id, organization_id=current_user.organization_id)
    ident = data["identification"]
    filename = f"relatorio-{ident['workshop_name']}-{data['year']}-{data['month']:02d}.docx"
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in filename)
    return Response(
        export.build_docx(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )
