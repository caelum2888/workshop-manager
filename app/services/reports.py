"""Relatório mensal (modelo "Relatório Mensal de Atividades: Oficinas Itinerantes").

Fluxo: generate_monthly_report() cria um RASCUNHO com dados calculados e
textos pré-consolidados -> pessoa revisa/edita (update_report) ->
finalize_report() congela identificação e métricas no snapshot.

Origem de cada informação:
- calculado  : métricas (services.metrics), nunca editáveis à mão.
- consolidado: textos montados a partir dos registros de aula (editáveis).
- humano     : avaliações que o sistema não tem como saber.
"""

import datetime as dt
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.report_summary import is_ai_configured, suggest_text
from app.models import ClassGroup, Lesson, MonthlyReport, Workshop
from app.services.errors import Conflict, InvalidData
from app.services.organizations import get_for_org
from app.services.metrics import get_monthly_metrics
from app.services.periods import month_bounds, period_dict

FIELD_ORIGIN = {
    "activities_summary": "consolidado",
    "methodology_rating": "humano",
    "methodology_notes": "consolidado",
    "infrastructure": "humano",
    "materials": "humano",
    "logistics_comments": "humano",
    "highlights_occurrences": "consolidado",
    "next_month_planning": "humano",
    "report_date": "humano",
    "signature_name": "humano",
}
EDITABLE_FIELDS = tuple(FIELD_ORIGIN)

REQUIRED_TO_FINALIZE = {
    "activities_summary": "Resumo das atividades realizadas",
    "methodology_rating": "Avaliação da metodologia",
    "infrastructure": "Infraestrutura",
    "materials": "Materiais",
    "report_date": "Data",
    "signature_name": "Assinatura do professor",
}

# Campo do relatório -> campo da aula usado como fonte.
SUGGESTION_SOURCES = {
    "activities_summary": "summary",
    "highlights_occurrences": "occurrences",
    "methodology_notes": "methodology",
}

METHODOLOGY_LABELS = {
    "excelente": ("Excelente", "Alunos engajados e objetivos atingidos."),
    "bom": ("Bom", "A maior parte do grupo acompanhou bem."),
    "regular": ("Regular", "Houve dificuldades de compreensão ou execução."),
    "atencao": ("Atenção", "Necessário mudar a estratégia para o próximo mês."),
}
INFRASTRUCTURE_LABELS = {"adequada": "Adequada", "inadequada": "Inadequada"}
MATERIALS_LABELS = {"suficientes": "Suficientes", "faltando": "Faltando"}


# ------------------------------------------------------------------ dados base
def _month_lessons(db: Session, workshop_id: int, year: int, month: int, organization_id: int) -> list[Lesson]:
    first, last = month_bounds(year, month)
    stmt = (
        select(Lesson)
        .join(ClassGroup)
        .where(
            Lesson.organization_id == organization_id,
            ClassGroup.organization_id == organization_id,
            ClassGroup.workshop_id == workshop_id,
            Lesson.date >= first,
            Lesson.date <= last,
        )
        .order_by(Lesson.date, ClassGroup.name)
    )
    return list(db.scalars(stmt))


def _entries(lessons: list[dict], lesson_field: str) -> list[dict]:
    return [
        {"date": l["date"], "class_name": l["class_name"], "text": l[lesson_field]}
        for l in lessons
        if l[lesson_field]
    ]


def build_monthly_report_data(
    db: Session, workshop_id: int, year: int, month: int, *, organization_id: int
) -> dict:
    """Tudo o que o sistema já sabe para o relatório do mês (sem gravar nada)."""
    workshop = get_for_org(db, Workshop, workshop_id, organization_id)
    lessons = [
        {
            "id": l.id,
            "date": l.date,
            "class_name": l.class_group.name,
            "summary": l.summary,
            "methodology": l.methodology,
            "occurrences": l.occurrences,
            "notes": l.notes,
        }
        for l in _month_lessons(db, workshop_id, year, month, organization_id)
    ]
    return {
        "identification": {
            "period": period_dict(year, month),
            "instructor_name": workshop.instructor.name if workshop.instructor else "",
            "workshop_name": workshop.name,
            "language": workshop.language,
            "location": workshop.location,
            "classes": [c.name for c in workshop.classes],
        },
        "metrics": get_monthly_metrics(
            db, year, month, workshop_id=workshop_id, organization_id=organization_id
        ),
        "records": {"lessons": lessons},
    }


# ------------------------------------------------------------- formatação
def _num(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def format_participation(metrics: dict) -> dict:
    """Textos da seção 3 (Dados de Participação) a partir das métricas."""
    enrolled = f"{metrics['enrolled_students']}"
    if metrics["active_students"] != metrics["enrolled_students"]:
        enrolled += f" ({metrics['active_students']} ativos ao final do mês)"

    if metrics["attendance_rate_percent"] is None:
        average = "Sem registros de presença no mês"
    else:
        average = (
            f"{_num(metrics['attendance_rate_percent'])}% — média de "
            f"{_num(metrics['average_present_per_lesson'])} alunos presentes por aula "
            f"({metrics['lessons_count']} aulas no mês)"
        )

    def names(items: list[dict]) -> str:
        return ": " + ", ".join(i["name"] for i in items) if items else ""

    changes = (
        f"{metrics['new_enrollments']} nova(s) matrícula(s){names(metrics['new_enrollment_list'])} / "
        f"{metrics['dropouts']} desistência(s){names(metrics['dropout_list'])}"
    )
    return {"enrolled": enrolled, "average": average, "changes": changes}


# -------------------------------------------------------------- operações
def _missing_fields(report: MonthlyReport) -> list[dict]:
    missing = []
    for field, label in REQUIRED_TO_FINALIZE.items():
        value = getattr(report, field)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append({"field": field, "label": label})
    return missing


def report_to_dict(db: Session, report: MonthlyReport, include_records: bool = True) -> dict:
    snapshot = json.loads(report.snapshot_json) if report.snapshot_json else None
    if snapshot:
        identification, metrics = snapshot["identification"], snapshot["metrics"]
        records = (
            build_monthly_report_data(
                db, report.workshop_id, report.year, report.month, organization_id=report.organization_id
            )["records"]
            if include_records
            else None
        )
    else:
        data = build_monthly_report_data(
            db, report.workshop_id, report.year, report.month, organization_id=report.organization_id
        )
        identification, metrics, records = data["identification"], data["metrics"], data["records"]

    result = {
        "id": report.id,
        "workshop_id": report.workshop_id,
        "year": report.year,
        "month": report.month,
        "period": period_dict(report.year, report.month),
        "status": report.status,
        "identification": identification,
        "metrics": metrics,
        "metrics_source": "snapshot" if snapshot else "live",
        "participation": format_participation(metrics),
        "fields": {field: getattr(report, field) for field in EDITABLE_FIELDS},
        "field_origin": FIELD_ORIGIN,
        "missing_for_finalization": _missing_fields(report),
        "ai_configured": is_ai_configured(),
        "created_at": report.created_at,
        "updated_at": report.updated_at,
        "finalized_at": report.finalized_at,
    }
    if include_records:
        result["records"] = records
    return result


def _find_report(
    db: Session, workshop_id: int, year: int, month: int, organization_id: int
) -> MonthlyReport | None:
    return db.scalar(
        select(MonthlyReport).where(
            MonthlyReport.organization_id == organization_id,
            MonthlyReport.workshop_id == workshop_id,
            MonthlyReport.year == year,
            MonthlyReport.month == month,
        )
    )


def generate_monthly_report(
    db: Session, workshop_id: int, year: int, month: int, use_ai: bool = False, *, organization_id: int
) -> dict:
    """Cria o rascunho do relatório do mês (ou devolve o existente, sem sobrescrever edições)."""
    get_for_org(db, Workshop, workshop_id, organization_id)
    existing = _find_report(db, workshop_id, year, month, organization_id)
    if existing:
        return {**report_to_dict(db, existing), "created": False, "warnings": []}

    data = build_monthly_report_data(db, workshop_id, year, month, organization_id=organization_id)
    lessons = data["records"]["lessons"]
    context = {
        "workshop_name": data["identification"]["workshop_name"],
        "period_label": data["identification"]["period"]["label"],
    }
    suggestions = {
        field: suggest_text(field, _entries(lessons, source), context, use_ai=use_ai)
        for field, source in SUGGESTION_SOURCES.items()
    }
    report = MonthlyReport(
        organization_id=organization_id,
        workshop_id=workshop_id,
        year=year,
        month=month,
        status="rascunho",
        activities_summary=suggestions["activities_summary"].text,
        highlights_occurrences=suggestions["highlights_occurrences"].text,
        methodology_notes=suggestions["methodology_notes"].text,
        report_date=dt.date.today(),
        signature_name=data["identification"]["instructor_name"],
    )
    db.add(report)
    db.commit()
    warnings = sorted({s.warning for s in suggestions.values() if s.warning and s.text})
    return {**report_to_dict(db, report), "created": True, "warnings": warnings}


def list_reports(
    db: Session,
    workshop_id: int | None = None,
    year: int | None = None,
    status: str | None = None,
    instructor_id: int | None = None,
    *,
    organization_id: int,
) -> list[dict]:
    stmt = (
        select(MonthlyReport)
        .where(MonthlyReport.organization_id == organization_id)
        .order_by(MonthlyReport.year.desc(), MonthlyReport.month.desc())
    )
    if workshop_id is not None:
        stmt = stmt.where(MonthlyReport.workshop_id == workshop_id)
    if year is not None:
        stmt = stmt.where(MonthlyReport.year == year)
    if status:
        stmt = stmt.where(MonthlyReport.status == status)
    if instructor_id is not None:
        stmt = stmt.join(Workshop).where(
            Workshop.instructor_id == instructor_id, Workshop.organization_id == organization_id
        )
    return [
        {
            "id": r.id,
            "workshop_id": r.workshop_id,
            "workshop_name": r.workshop.name,
            "period": period_dict(r.year, r.month),
            "status": r.status,
            "updated_at": r.updated_at,
            "finalized_at": r.finalized_at,
        }
        for r in db.scalars(stmt)
    ]


def get_report(db: Session, report_id: int, *, organization_id: int) -> dict:
    return report_to_dict(db, get_for_org(db, MonthlyReport, report_id, organization_id))


def update_report(db: Session, report_id: int, data: dict, *, organization_id: int) -> dict:
    report = get_for_org(db, MonthlyReport, report_id, organization_id)
    if report.status == "final":
        raise Conflict("Relatório finalizado. Reabra-o para editar.")
    for field, value in data.items():
        if field not in EDITABLE_FIELDS:
            continue
        if isinstance(value, str):
            value = value.strip()
        if field in ("activities_summary", "methodology_notes", "logistics_comments",
                     "highlights_occurrences", "next_month_planning", "signature_name") and value is None:
            value = ""
        setattr(report, field, value)
    db.commit()
    return report_to_dict(db, report)


def finalize_report(db: Session, report_id: int, *, organization_id: int) -> dict:
    report = get_for_org(db, MonthlyReport, report_id, organization_id)
    if report.status == "final":
        raise Conflict("Este relatório já está finalizado.")
    missing = _missing_fields(report)
    if missing:
        labels = ", ".join(m["label"] for m in missing)
        raise InvalidData(f"Preencha antes de finalizar: {labels}.", {"missing": missing})

    data = build_monthly_report_data(
        db, report.workshop_id, report.year, report.month, organization_id=organization_id
    )
    report.snapshot_json = json.dumps(
        {"identification": data["identification"], "metrics": data["metrics"]},
        default=str,
        ensure_ascii=False,
    )
    report.status = "final"
    report.finalized_at = dt.datetime.now()
    db.commit()
    return report_to_dict(db, report)


def reopen_report(db: Session, report_id: int, *, organization_id: int) -> dict:
    report = get_for_org(db, MonthlyReport, report_id, organization_id)
    report.status = "rascunho"
    report.snapshot_json = None
    report.finalized_at = None
    db.commit()
    return report_to_dict(db, report)


def suggest_report_text(
    db: Session, report_id: int, field: str, use_ai: bool = True, *, organization_id: int
) -> dict:
    """Sugestão (não salva) para um campo textual, a partir dos registros do mês."""
    report = get_for_org(db, MonthlyReport, report_id, organization_id)
    if field not in SUGGESTION_SOURCES:
        raise InvalidData(f"Campo sem fonte de registros: {field}.")
    data = build_monthly_report_data(
        db, report.workshop_id, report.year, report.month, organization_id=organization_id
    )
    context = {
        "workshop_name": data["identification"]["workshop_name"],
        "period_label": data["identification"]["period"]["label"],
    }
    entries = _entries(data["records"]["lessons"], SUGGESTION_SOURCES[field])
    return {"field": field, **suggest_text(field, entries, context, use_ai=use_ai).to_dict()}
