from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models import User
from app.routers.common import resolve_month
from app.schemas import AttendanceIn, LessonIn, LessonUpdate
from app.services import attendance, lessons
from app.services import access

router = APIRouter(prefix="/api", tags=["aulas e presença"])


@router.get(
    "/lessons",
    summary="Listar aulas",
    description="Filtre por year+month para obter as aulas do mês.",
)
def list_lessons(
    year: int | None = None,
    month: int | None = None,
    workshop_id: int | None = None,
    class_id: int | None = None,
    include_attendance: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if workshop_id is not None:
        access.ensure_workshop_access(db, current_user, workshop_id)
    if class_id is not None:
        access.ensure_class_access(db, current_user, class_id)
    instructor_id = access.professor_instructor_id(current_user)
    if year is not None or month is not None:
        year, month = resolve_month(year, month)
        return lessons.get_monthly_lessons(
            db, year, month, workshop_id, class_id, include_attendance, instructor_id,
            organization_id=current_user.organization_id,
        )
    return lessons.list_lessons(db, workshop_id=workshop_id, class_id=class_id,
                                include_attendance=include_attendance,
                                instructor_id=instructor_id,
                                organization_id=current_user.organization_id)


@router.get("/lessons/{lesson_id}", summary="Detalhar aula (com presenças)")
def get_lesson(lesson_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_lesson_access(db, current_user, lesson_id)
    return lessons.get_lesson(db, lesson_id, organization_id=current_user.organization_id)


@router.post(
    "/lessons",
    status_code=201,
    summary="Registrar aula com presença",
    description="Cria a aula e grava a presença em uma operação. "
    "Retorna 409 (com details.lesson_id) se a turma já tem aula nessa data.",
)
def create_lesson(payload: LessonIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_class_access(db, current_user, payload.class_id)
    return lessons.create_lesson(
        db, payload.model_dump(), current_user.id, organization_id=current_user.organization_id
    )


@router.patch("/lessons/{lesson_id}", summary="Atualizar aula e/ou presença")
def update_lesson(lesson_id: int, payload: LessonUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_lesson_access(db, current_user, lesson_id)
    return lessons.update_lesson(
        db, lesson_id, payload.model_dump(exclude_unset=True), current_user.id,
        organization_id=current_user.organization_id,
    )


@router.delete("/lessons/{lesson_id}", summary="Excluir aula")
def delete_lesson(lesson_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return lessons.delete_lesson(db, lesson_id, organization_id=admin.organization_id)


@router.post(
    "/attendance",
    summary="Registrar/atualizar presença de uma aula",
    description="Upsert: um único registro por aluno por aula.",
)
def record_attendance(payload: AttendanceIn, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_lesson_access(db, current_user, payload.lesson_id)
    records = [r.model_dump() for r in payload.records]
    return attendance.register_attendance(
        db, payload.lesson_id, records, current_user.id, organization_id=current_user.organization_id
    )


@router.get("/attendance/monthly", summary="Grade mensal de presença")
def get_monthly_attendance(
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
    return attendance.get_monthly_attendance(
        db, year, month, workshop_id, class_id,
        access.professor_instructor_id(current_user),
        organization_id=current_user.organization_id,
    )
