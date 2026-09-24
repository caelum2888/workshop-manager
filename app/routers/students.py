from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models import User
from app.schemas import DropoutIn, StudentIn, StudentStatus, StudentUpdate
from app.services import attendance, students
from app.services import access

router = APIRouter(prefix="/api/students", tags=["alunos"])


@router.get("", summary="Listar alunos")
def list_students(
    class_id: int | None = None,
    workshop_id: int | None = None,
    status: StudentStatus | None = None,
    q: str | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if class_id is not None:
        access.ensure_class_access(db, current_user, class_id)
    if workshop_id is not None:
        access.ensure_workshop_access(db, current_user, workshop_id)
    return students.list_students(
        db, class_id, workshop_id, status, q,
        access.professor_instructor_id(current_user),
        organization_id=current_user.organization_id,
    )


@router.get("/{student_id}", summary="Detalhar aluno")
def get_student(student_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_student_access(db, current_user, student_id)
    return students.get_student(db, student_id, organization_id=current_user.organization_id)


@router.get(
    "/{student_id}/frequency",
    summary="Frequência do aluno",
    description="Sem year/month: todo o histórico. Com year e month: apenas o mês.",
)
def get_student_frequency(
    student_id: int, year: int | None = None, month: int | None = None,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    access.ensure_student_access(db, current_user, student_id)
    return attendance.get_student_frequency(
        db, student_id, year, month, organization_id=current_user.organization_id
    )


@router.post("", status_code=201, summary="Matricular aluno")
def create_student(payload: StudentIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return students.create_student(db, payload.model_dump(), admin.organization_id)


@router.patch("/{student_id}", summary="Atualizar aluno")
def update_student(student_id: int, payload: StudentUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return students.update_student(
        db, student_id, payload.model_dump(exclude_unset=True), organization_id=admin.organization_id
    )


@router.post("/{student_id}/dropout", summary="Registrar desistência")
def register_dropout(student_id: int, payload: DropoutIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return students.register_dropout(
        db, student_id, payload.dropout_date, organization_id=admin.organization_id
    )


@router.post("/{student_id}/reactivate", summary="Reativar aluno")
def reactivate_student(student_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return students.reactivate_student(db, student_id, organization_id=admin.organization_id)
