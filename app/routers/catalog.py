import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user, require_admin
from app.models import User
from app.schemas import ClassIn, ClassUpdate, InstructorIn, InstructorUpdate, WorkshopIn, WorkshopUpdate
from app.services import catalog, lessons
from app.services import access

router = APIRouter(prefix="/api", tags=["cadastros"])


# ---- professores (model interno ainda chamado Instructor)
@router.get("/instructors", summary="Listar professores")
def list_instructors(active: bool | None = None, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.list_instructors(db, active, organization_id=admin.organization_id)


@router.post(
    "/instructors", status_code=201,
    summary="Cadastrar professor (uso avançado; a UI cadastra por /api/users)",
)
def create_instructor(payload: InstructorIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.create_instructor(db, payload.model_dump(), admin.organization_id)


@router.patch("/instructors/{instructor_id}", summary="Atualizar professor")
def update_instructor(instructor_id: int, payload: InstructorUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.update_instructor(
        db, instructor_id, payload.model_dump(exclude_unset=True), organization_id=admin.organization_id
    )


# ---- oficinas
@router.get("/workshops", summary="Listar oficinas")
def list_workshops(active: bool | None = None, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return catalog.list_workshops(
        db, active, access.professor_instructor_id(current_user), organization_id=current_user.organization_id
    )


@router.get("/workshops/{workshop_id}", summary="Detalhar oficina (com turmas)")
def get_workshop(workshop_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_workshop_access(db, current_user, workshop_id)
    return catalog.get_workshop(db, workshop_id, organization_id=current_user.organization_id)


@router.post("/workshops", status_code=201, summary="Cadastrar oficina")
def create_workshop(payload: WorkshopIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.create_workshop(db, payload.model_dump(), admin.organization_id)


@router.patch("/workshops/{workshop_id}", summary="Atualizar oficina")
def update_workshop(workshop_id: int, payload: WorkshopUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.update_workshop(
        db, workshop_id, payload.model_dump(exclude_unset=True), organization_id=admin.organization_id
    )


# ---- turmas
@router.get("/classes", summary="Listar turmas")
def list_classes(
    workshop_id: int | None = None,
    active: bool | None = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if workshop_id is not None:
        access.ensure_workshop_access(db, current_user, workshop_id)
    return catalog.list_classes(
        db, workshop_id, active, access.professor_instructor_id(current_user),
        organization_id=current_user.organization_id,
    )


@router.get("/classes/{class_id}", summary="Detalhar turma")
def get_class(class_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    access.ensure_class_access(db, current_user, class_id)
    return catalog.get_class(db, class_id, organization_id=current_user.organization_id)


@router.get(
    "/classes/{class_id}/roster",
    summary="Chamada da turma em uma data",
    description="Alunos matriculados na data e, se já existir, a aula registrada com as presenças marcadas.",
)
def get_class_roster(
    class_id: int,
    date: dt.date = Query(default_factory=dt.date.today),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    access.ensure_class_access(db, current_user, class_id)
    return lessons.get_roster(db, class_id, date, organization_id=current_user.organization_id)


@router.post("/classes", status_code=201, summary="Cadastrar turma")
def create_class(payload: ClassIn, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.create_class(db, payload.model_dump(), admin.organization_id)


@router.patch("/classes/{class_id}", summary="Atualizar turma")
def update_class(class_id: int, payload: ClassUpdate, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return catalog.update_class(
        db, class_id, payload.model_dump(exclude_unset=True), organization_id=admin.organization_id
    )
