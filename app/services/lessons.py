"""Aulas: registro (com presença), edição e chamada da turma."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ATTENDANCE_STATUSES, ClassGroup, Lesson
from app.services.attendance import enrolled_students, upsert_attendance
from app.services.catalog import class_to_dict
from app.services.errors import Conflict, InvalidData
from app.services.organizations import get_for_org
from app.services.periods import month_bounds

OPTIONAL_TEXT_FIELDS = ("methodology", "occurrences", "notes")
LESSON_CREATE_FIELDS = frozenset(
    {"class_id", "date", "summary", "methodology", "occurrences", "notes", "attendance"}
)
LESSON_UPDATE_FIELDS = LESSON_CREATE_FIELDS - {"class_id"}


def _validated_fields(data: dict, allowed: frozenset[str]) -> dict:
    """Impede mass assignment de tenant, turma e relacionamentos ORM."""
    unexpected = sorted(set(data) - allowed)
    if unexpected:
        raise InvalidData("Um ou mais campos não são permitidos.", {"fields": unexpected})
    return dict(data)


def lesson_to_dict(lesson: Lesson, include_attendance: bool = True) -> dict:
    class_group = lesson.class_group
    counts = {status: 0 for status in ATTENDANCE_STATUSES}
    for att in lesson.attendances:
        counts[att.status] += 1
    result = {
        "id": lesson.id,
        "class_id": lesson.class_id,
        "class_name": class_group.name,
        "workshop_id": class_group.workshop_id,
        "workshop_name": class_group.workshop.name,
        "date": lesson.date,
        "summary": lesson.summary,
        "methodology": lesson.methodology,
        "occurrences": lesson.occurrences,
        "notes": lesson.notes,
        "attendance_counts": {
            "present": counts["presente"],
            "absent": counts["ausente"],
            "excused": counts["atestado"],
            "total": len(lesson.attendances),
        },
        "created_at": lesson.created_at,
        "updated_at": lesson.updated_at,
    }
    if include_attendance:
        result["attendance"] = sorted(
            (
                {"student_id": a.student_id, "student_name": a.student.name, "status": a.status}
                for a in lesson.attendances
            ),
            key=lambda r: r["student_name"],
        )
    return result


def _clean_text(data: dict) -> dict:
    if "summary" in data and data["summary"] is not None:
        data["summary"] = data["summary"].strip()
    for field in OPTIONAL_TEXT_FIELDS:
        if field in data and data[field] is not None:
            data[field] = data[field].strip() or None
    return data


def find_lesson(db: Session, class_id: int, day: dt.date, organization_id: int) -> Lesson | None:
    return db.scalar(
        select(Lesson).where(
            Lesson.class_id == class_id, Lesson.date == day, Lesson.organization_id == organization_id
        )
    )


def get_roster(db: Session, class_id: int, day: dt.date, *, organization_id: int) -> dict:
    """Chamada da turma na data: alunos matriculados + aula já registrada (se houver)."""
    class_group = get_for_org(db, ClassGroup, class_id, organization_id)
    lesson = find_lesson(db, class_id, day, organization_id)
    marked = {a.student_id: a for a in lesson.attendances} if lesson else {}

    students = {s.id: s for s in enrolled_students(db, class_id, day, organization_id)}
    for att in marked.values():  # mantém quem já tem presença registrada
        students.setdefault(att.student_id, att.student)

    return {
        "class": class_to_dict(db, class_group),
        "date": day,
        "lesson": lesson_to_dict(lesson, include_attendance=False) if lesson else None,
        "students": [
            {
                "id": s.id,
                "name": s.name,
                "status": s.status,
                "attendance_status": marked[s.id].status if s.id in marked else None,
            }
            for s in sorted(students.values(), key=lambda s: s.name)
        ],
    }


def list_lessons(
    db: Session,
    year: int | None = None,
    month: int | None = None,
    workshop_id: int | None = None,
    class_id: int | None = None,
    include_attendance: bool = False,
    instructor_id: int | None = None,
    *,
    organization_id: int,
) -> list[dict]:
    """Aulas filtradas (ex.: get_monthly_lessons = year + month)."""
    stmt = (
        select(Lesson)
        .join(ClassGroup)
        .where(Lesson.organization_id == organization_id, ClassGroup.organization_id == organization_id)
        .order_by(Lesson.date.desc(), ClassGroup.name)
    )
    if year and month:
        first, last = month_bounds(year, month)
        stmt = stmt.where(Lesson.date >= first, Lesson.date <= last)
    if workshop_id is not None:
        stmt = stmt.where(ClassGroup.workshop_id == workshop_id)
    if class_id is not None:
        stmt = stmt.where(Lesson.class_id == class_id)
    if instructor_id is not None:
        stmt = stmt.where(ClassGroup.workshop.has(instructor_id=instructor_id))
    return [lesson_to_dict(l, include_attendance) for l in db.scalars(stmt)]


def get_monthly_lessons(
    db: Session,
    year: int,
    month: int,
    workshop_id: int | None = None,
    class_id: int | None = None,
    include_attendance: bool = False,
    instructor_id: int | None = None,
    *,
    organization_id: int,
) -> list[dict]:
    """Operação explícita e reutilizável para obter as aulas de um mês."""
    return list_lessons(
        db, year, month, workshop_id, class_id, include_attendance, instructor_id,
        organization_id=organization_id,
    )


def get_lesson(db: Session, lesson_id: int, *, organization_id: int) -> dict:
    return lesson_to_dict(get_for_org(db, Lesson, lesson_id, organization_id))


def create_lesson(
    db: Session, data: dict, user_id: int | None = None, *, organization_id: int
) -> dict:
    """Registra uma aula e sua presença em uma única operação."""
    data = _clean_text(_validated_fields(data, LESSON_CREATE_FIELDS))
    records = data.pop("attendance", None) or []
    get_for_org(db, ClassGroup, data["class_id"], organization_id)

    existing = find_lesson(db, data["class_id"], data["date"], organization_id)
    if existing:
        raise Conflict(
            "Já existe uma aula registrada para esta turma nesta data.",
            {"lesson_id": existing.id},
        )

    lesson = Lesson(
        **data,
        organization_id=organization_id,
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
    )
    db.add(lesson)
    try:
        # Uma aula salva é uma aula finalizada: a chamada precisa estar completa.
        # Turma vazia é válida e, nesse caso, a lista completa é naturalmente vazia.
        upsert_attendance(db, lesson, records, require_complete=True)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return lesson_to_dict(lesson)


def update_lesson(
    db: Session, lesson_id: int, data: dict, user_id: int | None = None, *, organization_id: int
) -> dict:
    lesson = get_for_org(db, Lesson, lesson_id, organization_id)
    data = _clean_text(_validated_fields(data, LESSON_UPDATE_FIELDS))
    records = data.pop("attendance", None)

    new_date = data.get("date")
    date_changed = bool(new_date and new_date != lesson.date)
    if date_changed:
        other = find_lesson(db, lesson.class_id, new_date, organization_id)
        if other:
            raise Conflict(
                "Já existe uma aula registrada para esta turma nesta data.",
                {"lesson_id": other.id},
            )
    try:
        for key, value in data.items():
            if key == "summary" and value is None:
                continue
            setattr(lesson, key, value)
        if records is not None:
            upsert_attendance(db, lesson, records, require_complete=True)
        elif date_changed:
            # Só a data mudou quem fazia parte da turma naquele dia pode ter mudado.
            # Sem uma nova chamada, só aceitamos a edição se as marcações atuais
            # ainda forem exatamente a lista completa da nova data.
            current = [
                {"student_id": attendance.student_id, "status": attendance.status}
                for attendance in lesson.attendances
            ]
            upsert_attendance(db, lesson, current, require_complete=True)
        # Nem attendance nem date mudaram: não reabre a validação de chamada completa.
        # Um aluno matriculado retroativamente DEPOIS que esta aula foi salva não pode
        # travar uma edição de texto (resumo/observações) que não mexe em presença.
        lesson.updated_by_user_id = user_id
        db.commit()
    except Exception:
        db.rollback()
        raise
    return lesson_to_dict(lesson)


def delete_lesson(db: Session, lesson_id: int, *, organization_id: int) -> dict:
    lesson = get_for_org(db, Lesson, lesson_id, organization_id)
    affected_attendance = len(lesson.attendances)
    try:
        db.delete(lesson)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return {"deleted": True, "id": lesson_id, "attendance_deleted": affected_attendance}
