"""Alunos: matrícula, desistência e reativação."""

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassGroup, Student
from app.services.errors import InvalidData
from app.services.organizations import get_for_org


STUDENT_FIELDS = frozenset(
    {"name", "class_id", "enrollment_date", "status", "dropout_date"}
)


def _validated_fields(data: dict) -> dict:
    """Impede que chamadas diretas ao service alterem campos internos/tenant."""
    unexpected = sorted(set(data) - STUDENT_FIELDS)
    if unexpected:
        raise InvalidData("Um ou mais campos não são permitidos.", {"fields": unexpected})
    return dict(data)


def student_to_dict(s: Student) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "class_id": s.class_id,
        "class_name": s.class_group.name,
        "workshop_id": s.class_group.workshop_id,
        "workshop_name": s.class_group.workshop.name,
        "status": s.status,
        "enrollment_date": s.enrollment_date,
        "dropout_date": s.dropout_date,
    }


def _validate(student: Student) -> None:
    if student.status == "ativo":
        student.dropout_date = None
    elif student.dropout_date is None:
        raise InvalidData("Informe a data de desistência.")
    if student.dropout_date and student.dropout_date < student.enrollment_date:
        raise InvalidData("A data de desistência não pode ser anterior à matrícula.")


def list_students(
    db: Session,
    class_id: int | None = None,
    workshop_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    instructor_id: int | None = None,
    *,
    organization_id: int,
) -> list[dict]:
    stmt = (
        select(Student)
        .join(ClassGroup)
        .where(Student.organization_id == organization_id, ClassGroup.organization_id == organization_id)
        .order_by(Student.name)
    )
    if class_id is not None:
        stmt = stmt.where(Student.class_id == class_id)
    if workshop_id is not None:
        stmt = stmt.where(ClassGroup.workshop_id == workshop_id)
    if status:
        stmt = stmt.where(Student.status == status)
    if q:
        stmt = stmt.where(Student.name.ilike(f"%{q}%"))
    if instructor_id is not None:
        stmt = stmt.where(ClassGroup.workshop.has(instructor_id=instructor_id))
    return [student_to_dict(s) for s in db.scalars(stmt)]


def get_student(db: Session, student_id: int, *, organization_id: int) -> dict:
    return student_to_dict(get_for_org(db, Student, student_id, organization_id))


def create_student(db: Session, data: dict, organization_id: int) -> dict:
    data = _validated_fields(data)
    get_for_org(db, ClassGroup, data["class_id"], organization_id)
    student = Student(**data, organization_id=organization_id)
    _validate(student)
    db.add(student)
    db.commit()
    return student_to_dict(student)


def update_student(db: Session, student_id: int, data: dict, *, organization_id: int) -> dict:
    student = get_for_org(db, Student, student_id, organization_id)
    data = _validated_fields(data)
    if data.get("class_id") is not None:
        get_for_org(db, ClassGroup, data["class_id"], organization_id)
    for key, value in data.items():
        setattr(student, key, value)
    try:
        _validate(student)
    except InvalidData:
        db.rollback()
        raise
    db.commit()
    return student_to_dict(student)


def register_dropout(db: Session, student_id: int, dropout_date: dt.date, *, organization_id: int) -> dict:
    return update_student(
        db, student_id, {"status": "desistente", "dropout_date": dropout_date}, organization_id=organization_id
    )


def reactivate_student(db: Session, student_id: int, *, organization_id: int) -> dict:
    return update_student(
        db, student_id, {"status": "ativo", "dropout_date": None}, organization_id=organization_id
    )
