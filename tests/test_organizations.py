"""v0.2 etapa 1: Organization e vínculo dos dados (sem isolamento entre organizações)."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

from types import SimpleNamespace

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app import config
from app.database import Base, make_engine
from app.models import (
    ClassGroup,
    Instructor,
    Lesson,
    MonthlyReport,
    Organization,
    Student,
    User,
    Workshop,
)
from app.services import auth, catalog, lessons, reports, students
from app.services.errors import InvalidData, NotFound
from app.services.organizations import DEFAULT_ORG_SLUG, get_or_create_default_organization
from tests.conftest import D

ROOT = Path(__file__).resolve().parent.parent
TENANT_TABLES = (User, Instructor, Workshop, ClassGroup, Student, Lesson, MonthlyReport)


def test_create_organization_defaults_and_unique_slug(db):
    org = Organization(name="Outra", slug="outra")
    db.add(org)
    db.commit()
    assert org.active is True
    assert org.created_at is not None

    db.add(Organization(name="Duplicada", slug="outra"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()

    db.add(Organization(name=None, slug="sem-nome"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_default_organization_is_created_once(db):
    first = get_or_create_default_organization(db)
    assert (first.name, first.slug) == ("Demo Organization", DEFAULT_ORG_SLUG)
    assert get_or_create_default_organization(db).id == first.id
    assert len(list(db.scalars(select(Organization)))) == 1


def test_admin_and_professor_belong_to_organization(client, db, setup):
    me = client.get("/api/me").json()
    assert me["organization_id"] == setup.workshop.organization_id
    assert me["organization_name"] == "Demo Organization"

    created = client.post(
        "/api/users",
        json={
            "name": "Professora Org",
            "email": "prof-org@example.test",
            "password": "senha-professor-123",
            "role": "professor",
            "workshop_ids": [setup.workshop.id],
        },
    )
    assert created.status_code == 201
    assert created.json()["organization_id"] == me["organization_id"]
    professor = db.get(User, created.json()["id"])
    assert professor.organization.slug == DEFAULT_ORG_SLUG
    assert professor.instructor.organization_id == me["organization_id"]

    # Login do professor segue funcionando e não exige organização no formulário.
    client.post("/logout")
    login = client.post(
        "/login",
        data={"email": "prof-org@example.test", "password": "senha-professor-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    assert client.get("/api/me").json()["organization_name"] == "Demo Organization"


def test_api_creates_business_records_in_users_organization(client, db, org):
    oid = org.id
    instructor = client.post("/api/instructors", json={"name": "Nova Instrutora"})
    workshop = client.post("/api/workshops", json={"name": "Teatro"})
    klass = client.post("/api/classes", json={"workshop_id": workshop.json()["id"], "name": "Teatro — 9h"})
    student = client.post("/api/students", json={"name": "Aluno API", "class_id": klass.json()["id"]})
    lesson = client.post(
        "/api/lessons",
        json={"class_id": klass.json()["id"], "date": "2026-09-01", "summary": "Aquecimento"},
    )
    report = client.post(
        "/api/reports/generate",
        json={"workshop_id": workshop.json()["id"], "year": 2026, "month": 9},
    )
    for response in (instructor, workshop, klass, student, lesson, report):
        assert response.status_code in (200, 201), response.text
    for model, response in (
        (Instructor, instructor),
        (Workshop, workshop),
        (ClassGroup, klass),
        (Student, student),
        (Lesson, lesson),
        (MonthlyReport, report),
    ):
        assert db.get(model, response.json()["id"]).organization_id == oid, model.__name__


def test_records_are_persisted_in_the_given_organization_not_the_default(db, org):
    other = Organization(name="Outra", slug="outra")
    db.add(other)
    db.commit()
    workshop = catalog.create_workshop(db, {"name": "Violão"}, other.id)
    klass = catalog.create_class(db, {"workshop_id": workshop["id"], "name": "Violão — 10h"}, other.id)
    student = students.create_student(
        db, {"name": "Aluno", "class_id": klass["id"], "enrollment_date": D(2026, 7, 1), "status": "ativo"}, other.id
    )
    lesson = lessons.create_lesson(
        db,
        {
            "class_id": klass["id"],
            "date": D(2026, 9, 1),
            "summary": "Aula",
            "attendance": [{"student_id": student["id"], "status": "presente"}],
        },
        organization_id=other.id,
    )
    report = reports.generate_monthly_report(db, workshop["id"], 2026, 9, organization_id=other.id)
    assert org.id != other.id
    for model, row in (
        (Workshop, workshop),
        (ClassGroup, klass),
        (Student, student),
        (Lesson, lesson),
        (MonthlyReport, report),
    ):
        assert db.get(model, row["id"]).organization_id == other.id, model.__name__


def test_organization_id_is_required_and_indexed(db, setup):
    db.add(Workshop(name="Sem organização"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    for model in TENANT_TABLES:
        column = model.__table__.c.organization_id
        assert not column.nullable and column.index, model.__name__
        assert {fk.column.table.name for fk in column.foreign_keys} == {"organizations"}


def _load_seed():
    spec = importlib.util.spec_from_file_location("seed_script", ROOT / "scripts" / "seed.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_seed_creates_initial_organization_and_no_orphans(monkeypatch, tmp_path):
    seed = _load_seed()
    engine = make_engine(f"sqlite:///{(tmp_path / 'seed.db').as_posix()}")
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(seed, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    monkeypatch.setattr(seed, "init_db", lambda: Base.metadata.create_all(engine))

    seed.seed()
    seed.seed()  # segunda execução não duplica nada

    with seed.SessionLocal() as db:
        orgs = list(db.scalars(select(Organization)))
        assert [(o.name, o.slug) for o in orgs] == [("Demo Organization", "demo")]
        for model in TENANT_TABLES[:-1]:  # o seed não cria relatórios
            rows = list(db.scalars(select(model)))
            assert rows, model.__name__
            assert {r.organization_id for r in rows} == {orgs[0].id}, model.__name__
        assert {u.role for u in db.scalars(select(User))} == {"admin", "professor"}
        assert len(list(db.scalars(select(User)))) == 3


def test_migration_assigns_existing_data_to_initial_organization(monkeypatch, tmp_path):
    """Banco v0.1 (sem organization_id) com dados reais é migrado sem perder nem orfanar nada."""
    path = tmp_path / "legacy.db"
    url = f"sqlite:///{path.as_posix()}"
    monkeypatch.setattr(config, "DATABASE_URL", url)
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))

    command.upgrade(cfg, "head")
    command.downgrade(cfg, "20260916_01")  # volta ao schema anterior às organizações

    con = sqlite3.connect(path)
    con.executescript(
        """
        INSERT INTO instructors (id, name, active) VALUES (1, 'Prof', 1);
        INSERT INTO users (id, name, email, password_hash, role, active, instructor_id)
            VALUES (1, 'Admin', 'a@x.test', 'x', 'admin', 1, NULL),
                   (2, 'Prof', 'p@x.test', 'x', 'professor', 1, 1);
        INSERT INTO workshops (id, name, language, location, instructor_id, active)
            VALUES (1, 'Desenho', '', '', 1, 1);
        INSERT INTO class_groups (id, workshop_id, name, active) VALUES (1, 1, 'T1', 1);
        INSERT INTO students (id, name, class_id, status, enrollment_date)
            VALUES (1, 'Ana', 1, 'ativo', '2026-07-01');
        INSERT INTO lessons (id, class_id, date, summary, created_by_user_id)
            VALUES (1, 1, '2026-09-01', 'Aula', 2);
        INSERT INTO attendances (lesson_id, student_id, status) VALUES (1, 1, 'presente');
        INSERT INTO monthly_reports (workshop_id, year, month, status, activities_summary,
            methodology_notes, logistics_comments, highlights_occurrences,
            next_month_planning, signature_name)
            VALUES (1, 2026, 9, 'rascunho', '', '', '', '', '', '');
        """
    )
    con.commit()
    con.close()

    command.upgrade(cfg, "head")

    engine = make_engine(url)
    with sessionmaker(bind=engine)() as db:
        (org,) = list(db.scalars(select(Organization)))
        assert (org.name, org.slug, org.active) == ("Demo Organization", "demo", True)
        for model in TENANT_TABLES:
            rows = list(db.scalars(select(model)))
            assert len(rows) >= 1, model.__name__
            assert {r.organization_id for r in rows} == {org.id}, model.__name__
        assert db.scalar(select(User).where(User.email == "p@x.test")).organization.slug == "demo"
        assert db.get(Lesson, 1).created_by_user_id == 2
        assert len(db.get(Lesson, 1).attendances) == 1

    # FK de autoria continua ON DELETE SET NULL após o rebuild da tabela no SQLite.
    with engine.begin() as conn:
        conn.exec_driver_sql("DELETE FROM attendances")
        conn.exec_driver_sql("DELETE FROM users WHERE id = 2")
        assert conn.exec_driver_sql("SELECT created_by_user_id FROM lessons").scalar() is None
    engine.dispose()


# ------------------------------------------------ ajuste 1: create_user exige organização
def _user_data(**extra):
    return {"name": "Fulana", "email": "fulana@example.test", "password": "senha-segura-123", "role": "admin", **extra}


def test_create_user_requires_explicit_organization(db, org):
    for data in (_user_data(), _user_data(organization_id=None)):
        with pytest.raises(InvalidData, match="organização"):
            auth.create_user(db, data)
    with pytest.raises(NotFound):
        auth.create_user(db, _user_data(organization_id=9999))
    assert db.scalar(select(User).where(User.email == "fulana@example.test")) is None

    created = auth.create_user(db, _user_data(organization_id=org.id))
    assert created["organization_id"] == org.id


def test_create_admin_script_resolves_initial_organization_explicitly(monkeypatch, session_factory):
    spec = importlib.util.spec_from_file_location("create_admin_script", ROOT / "scripts" / "create_admin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "SessionLocal", session_factory)
    monkeypatch.setattr(module, "init_db", lambda: None)
    monkeypatch.setattr(module.getpass, "getpass", lambda _prompt: "senha-segura-123")
    monkeypatch.setattr(sys, "argv", ["create_admin.py", "--name", "Coord", "--email", "coord@example.test"])

    assert module.main() == 0
    with session_factory() as db:
        user = db.scalar(select(User).where(User.email == "coord@example.test"))
        assert user.role == "admin" and user.organization.slug == DEFAULT_ORG_SLUG


# ------------------------------------------------ relações cross-organization: o outro tenant "não existe"
@pytest.fixture
def other_org(db, setup):
    """Segunda organização com oficineiro, oficina, turma e aluno próprios."""
    other = Organization(name="Outra", slug="outra")
    db.add(other)
    db.commit()
    instructor = catalog.create_instructor(db, {"name": "Instrutor B"}, other.id)
    workshop = catalog.create_workshop(db, {"name": "Violão", "instructor_id": instructor["id"]}, other.id)
    klass = catalog.create_class(db, {"workshop_id": workshop["id"], "name": "Violão — 10h"}, other.id)
    student = students.create_student(
        db, {"name": "Aluno B", "class_id": klass["id"], "enrollment_date": D(2026, 7, 1), "status": "ativo"}, other.id
    )
    return SimpleNamespace(org=other, instructor=instructor, workshop=workshop, klass=klass, student=student)


def _count(db, model):
    return len(list(db.scalars(select(model))))


def test_class_cannot_reference_workshop_of_another_organization(db, setup, other_org):
    before = _count(db, ClassGroup)
    with pytest.raises(NotFound, match="Recurso não encontrado"):
        catalog.create_class(db, {"workshop_id": other_org.workshop["id"], "name": "Intrusa"}, setup.org_id)
    assert _count(db, ClassGroup) == before


def test_student_cannot_enroll_in_class_of_another_organization(db, setup, other_org):
    before = _count(db, Student)
    with pytest.raises(NotFound):
        students.create_student(
            db,
            {"name": "Intruso", "class_id": other_org.klass["id"], "enrollment_date": D(2026, 7, 1), "status": "ativo"},
            setup.org_id,
        )
    assert _count(db, Student) == before


def test_student_cannot_be_moved_to_class_of_another_organization(db, setup, other_org):
    student = students.create_student(
        db,
        {"name": "Ana", "class_id": setup.klass.id, "enrollment_date": D(2026, 7, 1), "status": "ativo"},
        setup.org_id,
    )
    with pytest.raises(NotFound):
        students.update_student(
            db, student["id"], {"class_id": other_org.klass["id"], "name": "Ana Nova"}, organization_id=setup.org_id
        )
    db.expire_all()
    row = db.get(Student, student["id"])
    assert (row.class_id, row.name, row.organization_id) == (setup.klass.id, "Ana", setup.org_id)


def test_lesson_cannot_be_created_for_class_of_another_organization(db, setup, other_org):
    before = _count(db, Lesson)
    with pytest.raises(NotFound):
        lessons.create_lesson(
            db,
            {"class_id": other_org.klass["id"], "date": D(2026, 9, 1), "summary": "x", "attendance": []},
            organization_id=setup.org_id,
        )
    assert _count(db, Lesson) == before


def test_report_cannot_be_generated_for_workshop_of_another_organization(db, setup, other_org):
    with pytest.raises(NotFound):
        reports.generate_monthly_report(db, other_org.workshop["id"], 2026, 9, organization_id=setup.org_id)
    assert _count(db, MonthlyReport) == 0
    # o dono continua conseguindo gerar
    ok = reports.generate_monthly_report(db, other_org.workshop["id"], 2026, 9, organization_id=other_org.org.id)
    assert ok["created"] is True


def test_workshop_cannot_use_instructor_of_another_organization(db, setup, other_org):
    before = _count(db, Workshop)
    with pytest.raises(NotFound):
        catalog.create_workshop(db, {"name": "Nova", "instructor_id": other_org.instructor["id"]}, setup.org_id)
    assert _count(db, Workshop) == before

    with pytest.raises(NotFound):
        catalog.update_workshop(
            db,
            setup.workshop.id,
            {"name": "Renomeada", "instructor_id": other_org.instructor["id"]},
            organization_id=setup.org_id,
        )
    db.expire_all()
    workshop = db.get(Workshop, setup.workshop.id)
    assert workshop.name == "Desenho" and workshop.instructor_id != other_org.instructor["id"]


def test_professor_cannot_receive_workshop_of_another_organization(db, setup, other_org):
    users_before, instructors_before = _count(db, User), _count(db, Instructor)
    with pytest.raises(InvalidData, match="não foram encontradas"):
        auth.create_user(
            db,
            _user_data(role="professor", organization_id=setup.org_id, workshop_ids=[other_org.workshop["id"]]),
        )
    assert (_count(db, User), _count(db, Instructor)) == (users_before, instructors_before)

    created = auth.create_user(
        db, _user_data(role="professor", organization_id=setup.org_id, workshop_ids=[setup.workshop.id])
    )
    with pytest.raises(InvalidData, match="não foram encontradas"):
        auth.update_user(
            db, created["id"], {"workshop_ids": [other_org.workshop["id"]]}, organization_id=setup.org_id
        )
    db.expire_all()
    assert db.get(Workshop, other_org.workshop["id"]).instructor_id == other_org.instructor["id"]
    assert db.get(Workshop, setup.workshop.id).instructor_id == db.get(User, created["id"]).instructor_id


def test_api_treats_cross_organization_references_as_not_found(client, setup, other_org):
    # o admin autenticado é da organização inicial; os pais abaixo são da outra
    for url, body in (
        ("/api/classes", {"workshop_id": other_org.workshop["id"], "name": "Intrusa"}),
        ("/api/students", {"name": "Intruso", "class_id": other_org.klass["id"]}),
        ("/api/lessons", {"class_id": other_org.klass["id"], "date": "2026-09-01"}),
        ("/api/reports/generate", {"workshop_id": other_org.workshop["id"], "year": 2026, "month": 9}),
        ("/api/workshops", {"name": "Nova", "instructor_id": other_org.instructor["id"]}),
    ):
        response = client.post(url, json=body)
        assert response.status_code == 404, (url, response.status_code, response.text)
        assert response.json()["detail"] == "Recurso não encontrado.", url
        assert "Outra" not in response.text and "Violão" not in response.text, url


# ------------------------------------------------ ajuste 3: organização inativa não autentica
def _login(client, email, password):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


@pytest.fixture
def account(session_factory):
    with session_factory() as db:
        org = get_or_create_default_organization(db)
        auth.create_user(db, _user_data(email="ativo@example.test", organization_id=org.id))
    return "ativo@example.test", "senha-segura-123"


def _set_org_active(session_factory, active):
    with session_factory() as db:
        db.scalar(select(Organization)).active = active
        db.commit()


def test_active_user_in_active_organization_can_login(anonymous_client, account):
    assert _login(anonymous_client, *account).status_code == 303
    assert anonymous_client.get("/api/me").status_code == 200


def test_active_user_in_inactive_organization_cannot_login(anonymous_client, session_factory, account):
    _set_org_active(session_factory, False)
    with session_factory() as db:
        assert db.scalar(select(User)).active is True  # o usuário em si continua ativo

    assert _login(anonymous_client, *account).status_code == 401
    assert anonymous_client.get("/api/me").status_code == 401


def test_deactivating_organization_ends_existing_sessions(anonymous_client, session_factory, account):
    assert _login(anonymous_client, *account).status_code == 303
    _set_org_active(session_factory, False)
    assert anonymous_client.get("/api/me").status_code == 401
    # a tela de login não redireciona em loop para uma sessão de organização inativa
    assert anonymous_client.get("/login", follow_redirects=False).status_code == 200
