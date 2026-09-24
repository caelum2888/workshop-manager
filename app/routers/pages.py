"""Páginas HTML. Só renderizam telas; as ações passam pela API JSON."""

import datetime as dt

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.database import get_db
from app.dependencies import get_page_user, require_admin_page
from app.models import User
from app.services import catalog, metrics, reports
from app.services import access
from app.services.export import as_date
from app.services.periods import MONTH_NAMES
from app.services.reports import INFRASTRUCTURE_LABELS, MATERIALS_LABELS, METHODOLOGY_LABELS

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def _date_br(value) -> str:
    value = as_date(value)
    return value.strftime("%d/%m/%Y") if value else ""


templates.env.filters["date_br"] = _date_br
templates.env.globals["MONTH_NAMES"] = MONTH_NAMES


def render(request: Request, name: str, active: str, current_user: User, **context):
    return templates.TemplateResponse(
        request, name, {"active": active, "current_user": current_user, **context}
    )


@router.get("/")
def home(request: Request, current_user: User = Depends(get_page_user), db: Session = Depends(get_db)):
    today = dt.date.today()
    summary = metrics.get_monthly_metrics(
        db, today.year, today.month,
        instructor_id=access.professor_instructor_id(current_user),
        organization_id=current_user.organization_id,
    )
    instructor_id = access.professor_instructor_id(current_user)
    return render(
        request,
        "home.html",
        "home",
        current_user,
        summary=summary,
        today=today,
        my_classes=catalog.list_classes(
            db, active=True, instructor_id=instructor_id, organization_id=current_user.organization_id
        ),
    )


@router.get("/aulas/registrar")
def lesson_register_page(request: Request, current_user: User = Depends(get_page_user)):
    return render(request, "lesson_form.html", "registrar", current_user)


@router.get("/aulas")
def lessons_page(request: Request, current_user: User = Depends(get_page_user)):
    return render(request, "lessons.html", "aulas", current_user)


@router.get("/dashboard")
def dashboard_page(request: Request, current_user: User = Depends(get_page_user)):
    return render(request, "dashboard.html", "dashboard", current_user)


@router.get("/alunos")
def students_page(request: Request, current_user: User = Depends(get_page_user)):
    return render(request, "students.html", "alunos", current_user)


@router.get("/turmas")
def classes_page(request: Request, current_user: User = Depends(require_admin_page)):
    return render(request, "classes.html", "turmas", current_user)


@router.get("/relatorios")
def reports_page(request: Request, current_user: User = Depends(get_page_user)):
    return render(request, "reports.html", "relatorios", current_user)


@router.get("/relatorios/{report_id}")
def report_review_page(request: Request, report_id: int, current_user: User = Depends(get_page_user), db: Session = Depends(get_db)):
    access.ensure_report_access(db, current_user, report_id)
    return render(request, "report_review.html", "relatorios", current_user, report_id=report_id)


@router.get("/relatorios/{report_id}/imprimir")
def report_print_page(request: Request, report_id: int, current_user: User = Depends(get_page_user), db: Session = Depends(get_db)):
    access.ensure_report_access(db, current_user, report_id)
    report = reports.get_report(db, report_id, organization_id=current_user.organization_id)
    return templates.TemplateResponse(
        request,
        "report_print.html",
        {
            "r": report,
            "current_user": current_user,
            "methodology_labels": METHODOLOGY_LABELS,
            "infrastructure_labels": INFRASTRUCTURE_LABELS,
            "materials_labels": MATERIALS_LABELS,
        },
    )
