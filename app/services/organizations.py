"""Organizações e a guarda de tenant usada por todo acesso a dados.

Toda entidade com `organization_id` deve ser lida via `get_for_org` (por id) ou
com `Model.organization_id == organization_id` no SQL (listagens/agregações).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Organization
from app.services.errors import NotFound

DEFAULT_ORG_NAME = "Demo Organization"
DEFAULT_ORG_SLUG = "demo"
NOT_FOUND_MESSAGE = "Recurso não encontrado."


def get_or_create_default_organization(db: Session) -> Organization:
    """Default organization used by the local/demo environment."""
    org = db.scalar(select(Organization).where(Organization.slug == DEFAULT_ORG_SLUG))
    if org is None:
        org = Organization(name=DEFAULT_ORG_NAME, slug=DEFAULT_ORG_SLUG)
        db.add(org)
        db.commit()
    return org


def get_for_org(db: Session, model, obj_id: int, organization_id: int):
    """Busca por id dentro da organização.

    Inexistente e pertencente a outra organização são indistinguíveis: mesmo
    erro, mesma mensagem (não revela que o recurso existe em outro tenant).
    """
    obj = db.scalar(select(model).where(model.id == obj_id, model.organization_id == organization_id))
    if obj is None:
        raise NotFound(NOT_FOUND_MESSAGE)
    return obj
