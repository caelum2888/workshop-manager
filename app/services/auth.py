"""Autenticação local e gestão simples de contas.

Senhas usam scrypt da biblioteca padrão com salt aleatório. O formato inclui
os parâmetros, permitindo elevar o custo futuramente sem invalidar hashes.
"""

import hashlib
import hmac
import os

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Instructor, Organization, USER_ROLES, User, Workshop
from app.services.catalog import get_or_404
from app.services.errors import Conflict, InvalidData, Unauthenticated
from app.services.organizations import get_for_org

SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1


def normalize_email(email: str) -> str:
    value = email.strip().lower()
    if "@" not in value or value.startswith("@") or value.endswith("@"):
        raise InvalidData("Informe um e-mail válido.")
    return value


def hash_password(password: str) -> str:
    if len(password) < 8:
        raise InvalidData("A senha deve ter pelo menos 8 caracteres.")
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_hex, digest_hex = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
        return hmac.compare_digest(actual, bytes.fromhex(digest_hex))
    except (ValueError, TypeError):
        return False


_DUMMY_HASH = hash_password("not-a-real-password")


def user_to_dict(user: User) -> dict:
    workshops = user.instructor.workshops if user.instructor else []
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "role": user.role,
        "active": user.active,
        "organization_id": user.organization_id,
        "organization_name": user.organization.name,
        "instructor_id": user.instructor_id,
        "workshop_ids": [workshop.id for workshop in workshops],
        "workshop_names": [workshop.name for workshop in workshops],
        "created_at": user.created_at,
    }


def authenticate_user(db: Session, email: str, password: str) -> User:
    try:
        normalized = normalize_email(email)
    except InvalidData:
        normalized = "invalid@example.invalid"
    user = db.scalar(select(User).where(User.email == normalized))
    valid = verify_password(password, user.password_hash if user else _DUMMY_HASH)
    if user is None or not valid or not user.can_authenticate:
        raise Unauthenticated("E-mail ou senha inválidos, ou usuário inativo.")
    return user


def _assign_workshops(db: Session, user: User, workshop_ids: list[int]) -> None:
    if user.role != "professor" or user.instructor is None:
        if workshop_ids:
            raise InvalidData("Somente professores podem receber oficinas.")
        return
    requested = set(workshop_ids)
    workshops = (
        list(
            db.scalars(
                select(Workshop).where(
                    Workshop.id.in_(requested), Workshop.organization_id == user.organization_id
                )
            )
        )
        if requested
        else []
    )
    found = {workshop.id for workshop in workshops}
    if found != requested:
        raise InvalidData("Uma ou mais oficinas não foram encontradas.", {"missing_ids": sorted(requested - found)})
    conflicts = [
        workshop.name
        for workshop in workshops
        if workshop.instructor_id not in (None, user.instructor_id)
    ]
    if conflicts:
        raise Conflict(
            "Uma ou mais oficinas já pertencem a outro professor.",
            {"workshops": conflicts},
        )
    for workshop in list(user.instructor.workshops):
        if workshop.id not in requested:
            workshop.instructor = None
    for workshop in workshops:
        workshop.instructor = user.instructor


def create_user(db: Session, data: dict) -> dict:
    role = data.get("role", "professor")
    if role not in USER_ROLES:
        raise InvalidData("Perfil de usuário inválido.")
    email = normalize_email(data["email"])
    if db.scalar(select(User.id).where(User.email == email)):
        raise Conflict("Já existe um usuário com este e-mail.")
    organization_id = data.get("organization_id")
    if organization_id is None:
        raise InvalidData("Informe a organização do usuário.")
    get_or_404(db, Organization, organization_id, "Organização")
    instructor = None
    if role == "professor":
        link_instructor_id = data.get("link_instructor_id")
        if link_instructor_id is not None:
            # Vínculo explícito (admin escolheu "vincular a uma conta" na UI): tem prioridade
            # sobre a heurística abaixo. Só permite ligar a um professor legado sem User.
            candidate = get_for_org(db, Instructor, link_instructor_id, organization_id)
            if candidate.user is not None:
                raise Conflict("Este professor já está vinculado a outra conta.")
            reusable = candidate
        else:
            requested = set(data.get("workshop_ids", []))
            selected = (
                list(
                    db.scalars(
                        select(Workshop).where(
                            Workshop.id.in_(requested), Workshop.organization_id == organization_id
                        )
                    )
                )
                if requested
                else []
            )
            owners = {workshop.instructor for workshop in selected if workshop.instructor is not None}
            reusable = next(iter(owners)) if len(owners) == 1 else None
            if reusable is not None and reusable.user is not None:
                reusable = None
        if reusable is not None:
            instructor = reusable
            instructor.name = data["name"].strip()
            instructor.email = email
            instructor.active = data.get("active", True)
        else:
            instructor = Instructor(
                organization_id=organization_id,
                name=data["name"].strip(),
                email=email,
                active=data.get("active", True),
            )
            db.add(instructor)
            db.flush()
    user = User(
        organization_id=organization_id,
        name=data["name"].strip(),
        email=email,
        password_hash=hash_password(data["password"]),
        role=role,
        active=data.get("active", True),
        instructor=instructor,
    )
    db.add(user)
    try:
        db.flush()
        _assign_workshops(db, user, data.get("workshop_ids", []))
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("Não foi possível criar o usuário; verifique e-mail e vínculos.") from exc
    except Exception:
        db.rollback()
        raise
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("Não foi possível criar o usuário; verifique e-mail e vínculos.") from exc
    return user_to_dict(user)


def list_users(db: Session, *, organization_id: int) -> list[dict]:
    stmt = select(User).where(User.organization_id == organization_id).order_by(User.name)
    return [user_to_dict(user) for user in db.scalars(stmt)]


def get_user(db: Session, user_id: int, *, organization_id: int) -> User:
    return get_for_org(db, User, user_id, organization_id)


def update_user(
    db: Session, user_id: int, data: dict, *, organization_id: int, acting_user_id: int | None = None
) -> dict:
    user = get_user(db, user_id, organization_id=organization_id)
    if data.get("active") is False and user.role == "admin":
        if acting_user_id is not None and user.id == acting_user_id:
            raise InvalidData("Você não pode desativar a própria conta.")
        other_active_admins = db.scalar(
            select(func.count(User.id)).where(
                User.organization_id == organization_id,
                User.role == "admin",
                User.active.is_(True),
                User.id != user.id,
            )
        )
        if not other_active_admins:
            raise InvalidData("Não é possível desativar o último administrador ativo da organização.")
    if "email" in data:
        email = normalize_email(data["email"])
        duplicate = db.scalar(select(User.id).where(User.email == email, User.id != user.id))
        if duplicate:
            raise Conflict("Já existe um usuário com este e-mail.")
        user.email = email
        if user.instructor:
            user.instructor.email = email
    if "name" in data:
        user.name = data["name"].strip()
        if user.instructor:
            user.instructor.name = user.name
    if "active" in data:
        user.active = data["active"]
        if user.instructor:
            user.instructor.active = user.active
    if data.get("password"):
        user.password_hash = hash_password(data["password"])
    try:
        if data.get("workshop_ids") is not None:
            _assign_workshops(db, user, data["workshop_ids"])
    except Exception:
        db.rollback()
        raise
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise Conflict("Não foi possível atualizar o usuário.") from exc
    return user_to_dict(user)


def change_password(
    db: Session, user: User, current_password: str, new_password: str, confirm_password: str
) -> None:
    if new_password != confirm_password:
        raise InvalidData("A confirmação não bate com a nova senha.")
    if not verify_password(current_password, user.password_hash):
        raise Unauthenticated("Senha atual incorreta.")
    user.password_hash = hash_password(new_password)
    db.commit()
