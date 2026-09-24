"""Indicadores mensais — cálculo 100% determinístico (SQL + Python).

Definições (também documentadas no README):
- enrolled_students  : alunos com matrícula ativa em algum dia do mês
                       (matrícula <= fim do mês e sem desistência antes do início).
- active_students    : desses, os que seguem matriculados no último dia do mês.
- new_enrollments    : data de matrícula dentro do mês.
- dropouts           : data de desistência dentro do mês.
- lessons_count      : aulas registradas no mês.
- attendance_rate_percent : presentes / registros válidos de presença do mês × 100
                       (atestado conta no denominador e é exibido à parte;
                       marcação ausente não vira falta).
- average_present_per_lesson : presentes / aulas.
- frequency_percent (por aluno): calculada exclusivamente por
                       services.attendance.calculate_student_frequency.
"""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassGroup, Lesson, Student, Workshop
from app.services.attendance import calculate_student_frequency, frequency_percent
from app.services.errors import InvalidData
from app.services.organizations import get_for_org
from app.services.periods import month_bounds, period_dict

LOWEST_FREQUENCY_LIMIT = 5


def was_enrolled_during(student: Student, first: dt.date, last: dt.date) -> bool:
    if student.enrollment_date > last:
        return False
    return student.dropout_date is None or student.dropout_date >= first


def _scope(
    db: Session,
    workshop_id: int | None,
    class_id: int | None,
    instructor_id: int | None,
    organization_id: int,
) -> dict:
    workshop = get_for_org(db, Workshop, workshop_id, organization_id) if workshop_id else None
    class_group = get_for_org(db, ClassGroup, class_id, organization_id) if class_id else None
    if workshop and class_group and class_group.workshop_id != workshop.id:
        raise InvalidData("A turma informada não pertence à oficina informada.")
    if class_group and not workshop:
        workshop = class_group.workshop
    return {
        "workshop_id": workshop.id if workshop else None,
        "workshop_name": workshop.name if workshop else None,
        "class_id": class_group.id if class_group else None,
        "class_name": class_group.name if class_group else None,
        "instructor_id": instructor_id,
    }


def _student_ref(s: Student, date_field: str) -> dict:
    return {
        "student_id": s.id,
        "name": s.name,
        "class_name": s.class_group.name,
        "date": getattr(s, date_field),
    }


def get_monthly_metrics(
    db: Session,
    year: int,
    month: int,
    workshop_id: int | None = None,
    class_id: int | None = None,
    instructor_id: int | None = None,
    *,
    organization_id: int,
) -> dict:
    """Indicadores do mês para todas as oficinas, uma oficina ou uma turma (só da organização)."""
    first, last = month_bounds(year, month)
    scope = _scope(db, workshop_id, class_id, instructor_id, organization_id)

    def scoped(stmt, entity):
        stmt = stmt.where(
            entity.organization_id == organization_id, ClassGroup.organization_id == organization_id
        )
        if scope["class_id"]:
            return stmt.where(ClassGroup.id == scope["class_id"])
        if scope["workshop_id"]:
            return stmt.where(ClassGroup.workshop_id == scope["workshop_id"])
        if scope["instructor_id"]:
            return stmt.where(ClassGroup.workshop.has(instructor_id=scope["instructor_id"]))
        return stmt

    students = list(db.scalars(scoped(select(Student).join(ClassGroup), Student).order_by(Student.name)))
    enrolled = [s for s in students if was_enrolled_during(s, first, last)]
    active = [s for s in enrolled if s.dropout_date is None or s.dropout_date > last]
    new_enrollments = [s for s in students if first <= s.enrollment_date <= last]
    dropouts = [s for s in students if s.dropout_date and first <= s.dropout_date <= last]

    month_filter = (Lesson.date >= first, Lesson.date <= last)
    lessons_count = len(
        db.scalars(scoped(select(Lesson.id).join(ClassGroup), Lesson).where(*month_filter)).all()
    )
    totals = {"presente": 0, "ausente": 0, "atestado": 0}
    enrolled_ids = {s.id for s in enrolled}
    student_frequency = []
    for s in students:
        if s.id not in enrolled_ids:
            continue
        calculation = calculate_student_frequency(db, s, first=first, last=last)
        totals["presente"] += calculation["present"]
        totals["ausente"] += calculation["absent"]
        totals["atestado"] += calculation["excused"]
        student_frequency.append(
            {
                "student_id": s.id,
                "name": s.name,
                "class_name": s.class_group.name,
                "status": s.status,
                **{key: value for key, value in calculation.items() if key != "records"},
            }
        )

    total_records = sum(totals.values())

    lowest = sorted(
        (r for r in student_frequency if r["lessons_recorded"] > 0),
        key=lambda r: (r["frequency_percent"], -r["absent"], r["name"]),
    )[:LOWEST_FREQUENCY_LIMIT]

    return {
        "period": period_dict(year, month),
        "scope": scope,
        "enrolled_students": len(enrolled),
        "active_students": len(active),
        "new_enrollments": len(new_enrollments),
        "dropouts": len(dropouts),
        "new_enrollment_list": [_student_ref(s, "enrollment_date") for s in new_enrollments],
        "dropout_list": [_student_ref(s, "dropout_date") for s in dropouts],
        "lessons_count": lessons_count,
        "total_present": totals["presente"],
        "total_absent": totals["ausente"],
        "total_excused": totals["atestado"],
        "total_attendance_records": total_records,
        "attendance_rate_percent": frequency_percent(totals["presente"], total_records),
        "average_present_per_lesson": round(totals["presente"] / lessons_count, 1)
        if lessons_count
        else None,
        "student_frequency": student_frequency,
        "lowest_frequency": lowest,
        "frequency_rule": {
            "formula": "presenças / registros válidos de presença × 100",
            "excused_policy": "atestado entra no denominador e não no numerador",
            "eligibility": "aulas da turma no intervalo [matrícula, desistência)",
            "unmarked_policy": "sem marcação não é convertido em ausência",
        },
    }
