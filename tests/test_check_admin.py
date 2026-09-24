from scripts.check_admin import has_active_admin
from app.models import User
from app.services import auth


def test_no_admin_yet(db):
    assert has_active_admin(db) is False


def test_active_admin_found(db, org):
    auth.create_user(db, {
        "name": "Coord", "email": "coord@example.test", "password": "senha-segura-123",
        "role": "admin", "organization_id": org.id,
    })
    assert has_active_admin(db) is True


def test_inactive_admin_does_not_count(db, org):
    # has_active_admin é um scanner de estado puro (usado no boot); o guard de "não desativar
    # o último admin" vive em auth.update_user, não aqui — então o estado é montado direto no
    # banco (pode surgir de dado legado) em vez de passar pelo update_user, que bloquearia isto.
    created = auth.create_user(db, {
        "name": "Coord", "email": "coord2@example.test", "password": "senha-segura-123",
        "role": "admin", "organization_id": org.id,
    })
    db.get(User, created["id"]).active = False
    db.commit()
    assert has_active_admin(db) is False


def test_professor_alone_does_not_count(db, org):
    auth.create_user(db, {
        "name": "Prof", "email": "prof@example.test", "password": "senha-segura-123",
        "role": "professor", "organization_id": org.id,
    })
    assert has_active_admin(db) is False
