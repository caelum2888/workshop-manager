from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from app.database import get_db
from app.dependencies import get_current_user, get_page_user, require_admin, require_admin_page
from app.models import User
from app.schemas import PasswordChangeIn, UserIn, UserUpdate
from app.services import auth
from app.services.errors import Unauthenticated

router = APIRouter(tags=["autenticação"])
templates = Jinja2Templates(directory=BASE_DIR / "app" / "templates")


def _safe_next(next_url: str | None) -> str:
    """Só aceita caminho relativo local (evita open redirect); senão, cai em '/'."""
    if next_url and next_url.startswith("/") and not next_url.startswith("//") and "://" not in next_url:
        return next_url
    return "/"


@router.get("/login", include_in_schema=False)
def login_page(request: Request, next_url: str | None = Query(None, alias="next"), db: Session = Depends(get_db)):
    user = db.get(User, request.session.get("user_id")) if request.session.get("user_id") else None
    if user is not None and user.can_authenticate:
        return RedirectResponse(_safe_next(next_url), status_code=303)
    request.session.clear()
    return templates.TemplateResponse(request, "login.html", {"error": None, "next": _safe_next(next_url)})


@router.post("/login", include_in_schema=False)
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    next_url: str = Form("/", alias="next"),
    db: Session = Depends(get_db),
):
    safe_next = _safe_next(next_url)
    try:
        user = auth.authenticate_user(db, email, password)
    except Unauthenticated as exc:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"error": exc.message, "email": email, "next": safe_next},
            status_code=401,
        )
    request.session.clear()
    request.session["user_id"] = user.id
    return RedirectResponse(safe_next, status_code=303)


@router.post("/logout", include_in_schema=False)
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/usuarios", include_in_schema=False)
def users_page(request: Request, current_user: User = Depends(require_admin_page)):
    return templates.TemplateResponse(
        request,
        "users.html",
        {"active": "usuarios", "current_user": current_user},
    )


@router.get("/senha", include_in_schema=False)
def password_page(request: Request, current_user: User = Depends(get_page_user)):
    return templates.TemplateResponse(request, "password.html", {"active": "senha", "current_user": current_user})


@router.get("/api/me", summary="Usuário autenticado")
def me(current_user: User = Depends(get_current_user)):
    return auth.user_to_dict(current_user)


@router.get("/api/users", summary="Listar usuários")
def list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    return auth.list_users(db, organization_id=admin.organization_id)


@router.post("/api/users", status_code=201, summary="Criar usuário")
def create_user(
    payload: UserIn,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return auth.create_user(db, {**payload.model_dump(), "organization_id": admin.organization_id})


@router.patch("/api/users/{user_id}", summary="Atualizar usuário")
def update_user(
    user_id: int,
    payload: UserUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return auth.update_user(
        db, user_id, payload.model_dump(exclude_unset=True),
        organization_id=admin.organization_id, acting_user_id=admin.id,
    )


@router.post("/api/me/password", status_code=204, summary="Alterar a própria senha")
def change_my_password(
    payload: PasswordChangeIn,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    auth.change_password(
        db, current_user, payload.current_password, payload.new_password, payload.confirm_password
    )
