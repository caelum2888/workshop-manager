"""Cadastros básicos: oficineiros, oficinas e turmas. Tudo escopado por organização."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ClassGroup, Instructor, Student, Workshop
from app.services.errors import Conflict, InvalidData, NotFound
from app.services.organizations import get_for_org


INSTRUCTOR_FIELDS = frozenset({"name", "email", "active"})
WORKSHOP_FIELDS = frozenset({"name", "language", "location", "instructor_id", "active"})
CLASS_FIELDS = frozenset({"workshop_id", "name", "schedule", "location", "active"})


def _validated_fields(data: dict, allowed: frozenset[str]) -> dict:
    """Impede mass assignment quando o service é chamado sem um schema HTTP."""
    unexpected = sorted(set(data) - allowed)
    if unexpected:
        raise InvalidData("Um ou mais campos não são permitidos.", {"fields": unexpected})
    return dict(data)


def get_or_404(db: Session, model, obj_id: int, label: str):
    """Busca por id de entidade SEM organização (ex.: Organization). Para dados de tenant use get_for_org."""
    obj = db.get(model, obj_id)
    if obj is None:
        raise NotFound(f"{label} {obj_id} não encontrado(a).")
    return obj


def _apply(obj, data: dict) -> None:
    for key, value in data.items():
        setattr(obj, key, value)


# ---------------------------------------------------------------- professores
def instructor_to_dict(i: Instructor) -> dict:
    # has_user: False identifica um cadastro legado de professor sem conta de acesso
    # (não é apagado; a UI mostra "sem acesso ao sistema" e oferece vincular a um User).
    return {"id": i.id, "name": i.name, "email": i.email, "active": i.active, "has_user": i.user is not None}


def list_instructors(db: Session, active: bool | None = None, *, organization_id: int) -> list[dict]:
    stmt = select(Instructor).where(Instructor.organization_id == organization_id).order_by(Instructor.name)
    if active is not None:
        stmt = stmt.where(Instructor.active == active)
    return [instructor_to_dict(i) for i in db.scalars(stmt)]


def create_instructor(db: Session, data: dict, organization_id: int) -> dict:
    data = _validated_fields(data, INSTRUCTOR_FIELDS)
    instructor = Instructor(**data, organization_id=organization_id)
    db.add(instructor)
    db.commit()
    return instructor_to_dict(instructor)


def update_instructor(db: Session, instructor_id: int, data: dict, *, organization_id: int) -> dict:
    instructor = get_for_org(db, Instructor, instructor_id, organization_id)
    data = _validated_fields(data, INSTRUCTOR_FIELDS)
    _apply(instructor, data)
    if instructor.user:
        instructor.user.name = instructor.name
        instructor.user.email = (instructor.email or instructor.user.email).strip().lower()
        instructor.user.active = instructor.active
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("E-mail já utilizado por outro usuário.") from exc
    return instructor_to_dict(instructor)


# ------------------------------------------------------------------- oficinas
def workshop_to_dict(w: Workshop) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "language": w.language,
        "location": w.location,
        "instructor_id": w.instructor_id,
        "instructor_name": w.instructor.name if w.instructor else None,
        "active": w.active,
    }


def list_workshops(
    db: Session,
    active: bool | None = None,
    instructor_id: int | None = None,
    *,
    organization_id: int,
) -> list[dict]:
    stmt = select(Workshop).where(Workshop.organization_id == organization_id).order_by(Workshop.name)
    if active is not None:
        stmt = stmt.where(Workshop.active == active)
    if instructor_id is not None:
        stmt = stmt.where(Workshop.instructor_id == instructor_id)
    return [workshop_to_dict(w) for w in db.scalars(stmt)]


def get_workshop(db: Session, workshop_id: int, *, organization_id: int) -> dict:
    workshop = get_for_org(db, Workshop, workshop_id, organization_id)
    result = workshop_to_dict(workshop)
    result["classes"] = [class_to_dict(db, c) for c in workshop.classes]
    return result


def create_workshop(db: Session, data: dict, organization_id: int) -> dict:
    data = _validated_fields(data, WORKSHOP_FIELDS)
    if data.get("instructor_id") is not None:
        get_for_org(db, Instructor, data["instructor_id"], organization_id)
    workshop = Workshop(**data, organization_id=organization_id)
    db.add(workshop)
    db.commit()
    return workshop_to_dict(workshop)


def update_workshop(db: Session, workshop_id: int, data: dict, *, organization_id: int) -> dict:
    workshop = get_for_org(db, Workshop, workshop_id, organization_id)
    data = _validated_fields(data, WORKSHOP_FIELDS)
    if data.get("instructor_id") is not None:
        get_for_org(db, Instructor, data["instructor_id"], organization_id)
    _apply(workshop, data)
    db.commit()
    return workshop_to_dict(workshop)


# --------------------------------------------------------------------- turmas
def class_to_dict(db: Session, c: ClassGroup) -> dict:
    active_students = db.scalar(
        select(func.count(Student.id)).where(
            Student.class_id == c.id,
            Student.organization_id == c.organization_id,
            Student.status == "ativo",
        )
    )
    return {
        "id": c.id,
        "workshop_id": c.workshop_id,
        "workshop_name": c.workshop.name,
        "name": c.name,
        "schedule": c.schedule,
        "location": c.location or c.workshop.location,
        "active": c.active,
        "active_students": active_students or 0,
    }


def list_classes(
    db: Session,
    workshop_id: int | None = None,
    active: bool | None = None,
    instructor_id: int | None = None,
    *,
    organization_id: int,
) -> list[dict]:
    stmt = (
        select(ClassGroup)
        .join(Workshop)
        .where(ClassGroup.organization_id == organization_id, Workshop.organization_id == organization_id)
        .order_by(Workshop.name, ClassGroup.name)
    )
    if workshop_id is not None:
        stmt = stmt.where(ClassGroup.workshop_id == workshop_id)
    if active is not None:
        stmt = stmt.where(ClassGroup.active == active)
    if instructor_id is not None:
        stmt = stmt.where(Workshop.instructor_id == instructor_id)
    return [class_to_dict(db, c) for c in db.scalars(stmt)]


def get_class(db: Session, class_id: int, *, organization_id: int) -> dict:
    return class_to_dict(db, get_for_org(db, ClassGroup, class_id, organization_id))


def create_class(db: Session, data: dict, organization_id: int) -> dict:
    data = _validated_fields(data, CLASS_FIELDS)
    get_for_org(db, Workshop, data["workshop_id"], organization_id)
    class_group = ClassGroup(**data, organization_id=organization_id)
    db.add(class_group)
    db.commit()
    return class_to_dict(db, class_group)


def update_class(db: Session, class_id: int, data: dict, *, organization_id: int) -> dict:
    class_group = get_for_org(db, ClassGroup, class_id, organization_id)
    # A oficina da turma é imutável na operação de edição atual.
    data = _validated_fields(data, CLASS_FIELDS - {"workshop_id"})
    _apply(class_group, data)
    db.commit()
    return class_to_dict(db, class_group)
