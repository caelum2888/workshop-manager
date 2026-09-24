"""Unificação de terminologia Professor/Oficineiro: porta única de cadastro (Usuários),
vínculo explícito a professor legado sem conta, autobloqueio de admin e alteração de senha."""
import pytest

from app.models import Instructor, User
from app.services import auth
from app.services.errors import InvalidData


def login(client, email, password):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


class TestPortaUnicaDeCadastro:
    def test_turmas_page_has_no_oficineiro_ui(self, client):
        """A UI não usa mais "Oficineiro" nem oferece cadastro separado; só o rótulo
        "Professor responsável" no select (a relação com Instructor continua interna)."""
        html = client.get("/turmas").text
        assert "oficineiro" not in html.lower()
        assert "Professor responsável" in html
        assert "dlgInstructor" not in html
        assert "newInstructor" not in html

    def test_creating_professor_links_instructor_automatically(self, client):
        resp = client.post("/api/users", json={
            "name": "Nova Prof", "email": "novaprof@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [],
        })
        assert resp.status_code == 201
        assert resp.json()["instructor_id"] is not None

    def test_professor_appears_in_workshop_instructor_select_source(self, client):
        created = client.post("/api/users", json={
            "name": "Prof Select", "email": "profselect@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [],
        }).json()
        instructors = client.get("/api/instructors").json()
        assert any(i["id"] == created["instructor_id"] for i in instructors)

    def test_editing_professor_does_not_create_duplicate_instructor(self, client, db):
        before = db.query(Instructor).count()
        created = client.post("/api/users", json={
            "name": "Prof X", "email": "profx@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [],
        }).json()
        client.patch(f"/api/users/{created['id']}", json={"name": "Prof X Editado"})
        after = db.query(Instructor).count()
        assert after == before + 1


class TestVincularProfessorLegado:
    def test_link_instructor_id_reuses_orphan_instructor(self, client, db, org):
        orphan = Instructor(organization_id=org.id, name="Legado Sem Conta", email="legado@example.test")
        db.add(orphan)
        db.commit()
        resp = client.post("/api/users", json={
            "name": "Legado Sem Conta", "email": "legado@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [], "link_instructor_id": orphan.id,
        })
        assert resp.status_code == 201
        assert resp.json()["instructor_id"] == orphan.id

    def test_link_instructor_id_rejects_instructor_already_linked(self, client):
        first = client.post("/api/users", json={
            "name": "Prof Um", "email": "um@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [],
        }).json()
        resp = client.post("/api/users", json={
            "name": "Prof Dois", "email": "dois@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [], "link_instructor_id": first["instructor_id"],
        })
        assert resp.status_code == 409

    def test_link_instructor_id_from_another_organization_is_not_found(self, client, db, org):
        from app.models import Organization

        other = Organization(name="Outra", slug="outra-unif")
        db.add(other)
        db.commit()
        foreign = Instructor(organization_id=other.id, name="De Outra Org")
        db.add(foreign)
        db.commit()
        resp = client.post("/api/users", json={
            "name": "Tentativa", "email": "tentativa@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [], "link_instructor_id": foreign.id,
        })
        assert resp.status_code == 404

    def test_legacy_instructor_without_user_is_flagged_and_not_deleted(self, client, db, org):
        orphan = Instructor(organization_id=org.id, name="Órfão", email=None)
        db.add(orphan)
        db.commit()
        instructors = client.get("/api/instructors").json()
        match = next(i for i in instructors if i["id"] == orphan.id)
        assert match["has_user"] is False
        client.get("/api/instructors")  # outra leitura não afeta nada
        assert db.get(Instructor, orphan.id) is not None


class TestImportadorTerminologia:
    def test_professor_email_is_the_official_column(self):
        from app.services import imports

        assert "professor_email" in imports.TEMPLATE_COLUMNS
        assert "oficineiro_email" not in imports.TEMPLATE_COLUMNS

    def test_oficineiro_email_still_accepted_as_legacy_alias(self):
        from app.services import imports

        assert imports.HEADER_ALIASES["oficineiro_email"] == "professor_email"


class TestAutobloqueioAdmin:
    def test_admin_cannot_deactivate_self(self, client):
        me = client.get("/api/me").json()
        resp = client.patch(f"/api/users/{me['id']}", json={"active": False})
        assert resp.status_code == 422
        assert "própria conta" in resp.json()["detail"]

    def test_admin_can_deactivate_another_admin_when_not_last(self, client):
        second = client.post("/api/users", json={
            "name": "Segundo Admin", "email": "segundo@example.test", "password": "senha-admin-123", "role": "admin",
        })
        assert second.status_code == 201
        resp = client.patch(f"/api/users/{second.json()['id']}", json={"active": False})
        assert resp.status_code == 200

    def test_last_active_admin_cannot_be_deactivated(self, db, org):
        created = auth.create_user(db, {
            "name": "Único Admin", "email": "unico@example.test", "password": "senha-segura-123",
            "role": "admin", "organization_id": org.id,
        })
        with pytest.raises(InvalidData, match="último administrador"):
            auth.update_user(db, created["id"], {"active": False}, organization_id=org.id)
        assert db.get(User, created["id"]).active is True

    def test_deactivating_one_of_two_admins_is_allowed(self, db, org):
        a = auth.create_user(db, {
            "name": "Admin A", "email": "a@example.test", "password": "senha-segura-123",
            "role": "admin", "organization_id": org.id,
        })
        auth.create_user(db, {
            "name": "Admin B", "email": "b@example.test", "password": "senha-segura-123",
            "role": "admin", "organization_id": org.id,
        })
        auth.update_user(db, a["id"], {"active": False}, organization_id=org.id)
        assert db.get(User, a["id"]).active is False


class TestAlterarSenha:
    def test_change_password_success_and_login_with_new_password(self, client):
        resp = client.post("/api/me/password", json={
            "current_password": "senha-teste-123",
            "new_password": "nova-senha-456",
            "confirm_password": "nova-senha-456",
        })
        assert resp.status_code == 204
        client.post("/logout")
        assert login(client, "admin@test.local", "nova-senha-456").status_code == 303

    def test_change_password_wrong_current_password_is_rejected(self, client):
        resp = client.post("/api/me/password", json={
            "current_password": "senha-errada",
            "new_password": "nova-senha-456",
            "confirm_password": "nova-senha-456",
        })
        assert resp.status_code == 401
        client.post("/logout")
        assert login(client, "admin@test.local", "senha-teste-123").status_code == 303

    def test_change_password_confirmation_mismatch_is_rejected(self, client):
        resp = client.post("/api/me/password", json={
            "current_password": "senha-teste-123",
            "new_password": "nova-senha-456",
            "confirm_password": "outra-coisa-789",
        })
        assert resp.status_code == 422
        client.post("/logout")
        assert login(client, "admin@test.local", "senha-teste-123").status_code == 303

    def test_change_password_available_to_professor_too(self, client, setup):
        client.post("/api/users", json={
            "name": "Prof Senha", "email": "profsenha@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [],
        })
        client.post("/logout")
        login(client, "profsenha@example.test", "senha-professor-123")
        resp = client.post("/api/me/password", json={
            "current_password": "senha-professor-123",
            "new_password": "nova-senha-789",
            "confirm_password": "nova-senha-789",
        })
        assert resp.status_code == 204
