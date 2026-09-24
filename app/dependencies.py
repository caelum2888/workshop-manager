"""Dependências HTTP reutilizáveis de autenticação e perfil."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.access import require_admin_user
from app.services.errors import Unauthenticated


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if user is None or not user.can_authenticate:
        request.session.clear()
        raise Unauthenticated("Faça login para continuar.")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    require_admin_user(current_user)
    return current_user


def get_page_user(request: Request, db: Session = Depends(get_db)) -> User:
    try:
        return get_current_user(request, db)
    except Unauthenticated as exc:
        path = request.url.path
        next_qs = f"?next={path}" if path and path != "/" else ""
        raise HTTPException(status_code=303, headers={"Location": f"/login{next_qs}"}) from exc


def require_admin_page(current_user: User = Depends(get_page_user)) -> User:
    require_admin_user(current_user)
    return current_user
