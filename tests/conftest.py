import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config
from app.database import Base, get_db, make_engine
from app.models import Attendance, ClassGroup, Instructor, Lesson, Student, Workshop
from app.services.auth import create_user
from app.services.organizations import get_or_create_default_organization

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "")


def _stamp_head(engine) -> None:
    """Carimba o schema de teste como equivalente ao head do Alembic.

    ``create_all`` reproduz o schema atual dos models (mesma garantia que o
    README já documenta), então isso representa fielmente "banco instalado e
    migrado" para app.readiness.schema_status, sem depender da engine global
    que o Alembic resolveria via DATABASE_URL.
    """
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"))
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": head})


@pytest.fixture
def session_factory():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    _stamp_head(engine)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def client(session_factory, monkeypatch):
    from app import main

    def override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(main, "ensure_database_ready", lambda *a, **k: None)
    main.app.dependency_overrides[get_db] = override
    with TestClient(main.app) as c:
        with session_factory() as session:
            create_user(
                session,
                {
                    "name": "Admin Teste",
                    "email": "admin@test.local",
                    "password": "senha-teste-123",
                    "role": "admin",
                    "organization_id": get_or_create_default_organization(session).id,
                },
            )
        response = c.post(
            "/login",
            data={"email": "admin@test.local", "password": "senha-teste-123"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        yield c
    main.app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client(session_factory, monkeypatch):
    from app import main

    def override():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(main, "ensure_database_ready", lambda *a, **k: None)
    main.app.dependency_overrides[get_db] = override
    with TestClient(main.app) as c:
        yield c
    main.app.dependency_overrides.clear()


@pytest.fixture
def org(db):
    return get_or_create_default_organization(db)


@pytest.fixture
def setup(db, org):
    oid = org.id
    instructor = Instructor(organization_id=oid, name="João Pereira")
    workshop = Workshop(organization_id=oid, name="Desenho", language="Artes Visuais", location="Vila Nova", instructor=instructor)
    klass = ClassGroup(organization_id=oid, workshop=workshop, name="Desenho — 14h30")
    other = ClassGroup(organization_id=oid, workshop=workshop, name="Desenho — 16h00")
    db.add_all([instructor, workshop, klass, other])
    db.commit()
    return SimpleNamespace(workshop=workshop, klass=klass, other=other, org_id=oid)


def add_student(db, klass, name, enrolled, dropout=None):
    student = Student(
        organization_id=klass.organization_id,
        name=name,
        class_group=klass,
        enrollment_date=enrolled,
        dropout_date=dropout,
        status="desistente" if dropout else "ativo",
    )
    db.add(student)
    db.commit()
    return student


def add_lesson(db, klass, day, marks=None, **fields):
    """marks: {student: status}"""
    lesson = Lesson(organization_id=klass.organization_id, class_group=klass, date=day, summary=fields.pop("summary", f"Aula {day}"), **fields)
    for student, status in (marks or {}).items():
        lesson.attendances.append(Attendance(student=student, status=status))
    db.add(lesson)
    db.commit()
    return lesson


D = dt.date
