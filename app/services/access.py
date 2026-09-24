"""Regras centralizadas de autorização: primeiro a organização (404), depois o perfil (403).

Recurso de outra organização é tratado como inexistente (404, sem revelar
nada). Já um recurso da própria organização fora do escopo do professor
continua sendo 403.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ClassGroup, Lesson, MonthlyReport, Student, User, Workshop
from app.services.errors import Forbidden
from app.services.organizations import get_for_org


def is_admin(user: User) -> bool:
    return user.role == "admin"


def professor_instructor_id(user: User) -> int | None:
    if is_admin(user):
        return None
    if user.instructor_id is None:
        raise Forbidden("Professor sem oficina vinculada.")
    return user.instructor_id


def accessible_workshop_ids(db: Session, user: User) -> set[int] | None:
    if is_admin(user):
        return None
    if user.instructor_id is None:
        return set()
    return set(
        db.scalars(
            select(Workshop.id).where(
                Workshop.instructor_id == user.instructor_id,
                Workshop.organization_id == user.organization_id,
            )
        )
    )


def require_admin_user(user: User) -> None:
    if not is_admin(user):
        raise Forbidden("Esta ação é exclusiva da coordenação.")


def ensure_workshop_access(db: Session, user: User, workshop_id: int) -> Workshop:
    workshop = get_for_org(db, Workshop, workshop_id, user.organization_id)
    allowed = accessible_workshop_ids(db, user)
    if allowed is not None and workshop.id not in allowed:
        raise Forbidden("Você não tem acesso a esta oficina.")
    return workshop


def ensure_class_access(db: Session, user: User, class_id: int) -> ClassGroup:
    class_group = get_for_org(db, ClassGroup, class_id, user.organization_id)
    ensure_workshop_access(db, user, class_group.workshop_id)
    return class_group


def ensure_student_access(db: Session, user: User, student_id: int) -> Student:
    student = get_for_org(db, Student, student_id, user.organization_id)
    ensure_class_access(db, user, student.class_id)
    return student


def ensure_lesson_access(db: Session, user: User, lesson_id: int) -> Lesson:
    lesson = get_for_org(db, Lesson, lesson_id, user.organization_id)
    ensure_class_access(db, user, lesson.class_id)
    return lesson


def ensure_report_access(db: Session, user: User, report_id: int) -> MonthlyReport:
    report = get_for_org(db, MonthlyReport, report_id, user.organization_id)
    ensure_workshop_access(db, user, report.workshop_id)
    return report
