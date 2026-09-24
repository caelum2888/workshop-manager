"""Existe pelo menos um administrador ativo? Usado por start.ps1 antes de subir o servidor.

Uso: python scripts/check_admin.py
Saída: código 0 se existe; 1 se não existe (ou o schema ainda não tem a tabela).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.database import SessionLocal  # noqa: E402
from app.models import User  # noqa: E402


def has_active_admin(db: Session) -> bool:
    return db.scalar(select(User.id).where(User.role == "admin", User.active.is_(True)).limit(1)) is not None


if __name__ == "__main__":
    with SessionLocal() as db:
        sys.exit(0 if has_active_admin(db) else 1)
