import datetime as dt

from app.models import ClassGroup, Instructor, Student, Workshop
from app.services import reports
from app.services.auth import create_user, verify_password
from app.services.organizations import get_or_create_default_organization
from tests.conftest import D, add_lesson, add_student


def login(client, email, password):
    return client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


def test_login_logout_wrong_password_and_protected_routes(anonymous_client, session_factory):
    with session_factory() as db:
        created = create_user(
            db,
            {
                "name": "Admin",
                "email": "admin@example.test",
                "password": "senha-segura-123",
                "role": "admin",
                "organization_id": get_or_create_default_organization(db).id,
            },
        )
        assert "password_hash" not in created

    assert anonymous_client.get("/api/me").status_code == 401
    assert anonymous_client.get("/health").json() == {"status": "ok"}
    page = anonymous_client.get("/", follow_redirects=False)
    assert page.status_code == 303
    assert page.headers["location"] == "/login"
    assert login(anonymous_client, "admin@example.test", "errada").status_code == 401
    successful = login(anonymous_client, "ADMIN@example.test", "senha-segura-123")
    assert successful.status_code == 303
    cookie = successful.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert anonymous_client.get("/api/me").json()["role"] == "admin"

    logout = anonymous_client.post("/logout", follow_redirects=False)
    assert logout.status_code == 303
    assert anonymous_client.get("/api/me").status_code == 401


def test_inactive_user_cannot_login(anonymous_client, session_factory):
    with session_factory() as db:
        create_user(
            db,
            {
                "name": "Inativo",
                "email": "inativo@example.test",
                "password": "senha-segura-123",
                "role": "professor",
                "active": False,
                "organization_id": get_or_create_default_organization(db).id,
            },
        )
    assert login(anonymous_client, "inativo@example.test", "senha-segura-123").status_code == 401


def test_password_is_hashed_and_duplicate_email_is_blocked(client, session_factory):
    body = {
        "name": "Professora",
        "email": "prof@example.test",
        "password": "senha-segura-123",
        "role": "professor",
        "workshop_ids": [],
    }
    created = client.post("/api/users", json=body)
    assert created.status_code == 201
    assert "password" not in created.text.lower()
    with session_factory() as db:
        from app.models import User

        user = db.get(User, created.json()["id"])
        assert user.password_hash != body["password"]
        assert user.password_hash.startswith("scrypt$")
        assert verify_password(body["password"], user.password_hash)

    duplicate = client.post("/api/users", json={**body, "name": "Outra"})
    assert duplicate.status_code == 409


def test_professor_scope_is_enforced_in_backend(client, db, setup):
    own_student = add_student(db, setup.klass, "Aluno próprio", D(2026, 7, 1))
    oid = setup.workshop.organization_id
    other_instructor = Instructor(organization_id=oid, name="Outra Professora", email="outra@example.test")
    other_workshop = Workshop(organization_id=oid, name="Outra Oficina", instructor=other_instructor)
    other_class = ClassGroup(organization_id=oid, workshop=other_workshop, name="Turma alheia")
    other_student = Student(
        organization_id=oid,
        name="Aluno alheio",
        class_group=other_class,
        enrollment_date=D(2026, 7, 1),
        status="ativo",
    )
    db.add_all([other_instructor, other_workshop, other_class, other_student])
    db.commit()
    other_lesson = add_lesson(
        db, other_class, D(2026, 9, 1), {other_student: "presente"}
    )
    other_report = reports.generate_monthly_report(db, other_workshop.id, 2026, 9, organization_id=oid)

    professor = client.post(
        "/api/users",
        json={
            "name": "Professor Próprio",
            "email": "professor@example.test",
            "password": "senha-professor-123",
            "role": "professor",
            "workshop_ids": [setup.workshop.id],
        },
    )
    assert professor.status_code == 201
    client.post("/logout")
    assert login(client, "professor@example.test", "senha-professor-123").status_code == 303

    assert client.get(f"/api/classes/{setup.klass.id}").status_code == 200
    assert client.get(f"/api/classes/{other_class.id}").status_code == 403
    assert client.get(f"/api/students/{own_student.id}").status_code == 200
    assert client.get(f"/api/students/{other_student.id}").status_code == 403
    assert client.get(f"/api/lessons/{other_lesson.id}").status_code == 403
    assert client.get(f"/api/reports/{other_report['id']}").status_code == 403
    assert client.get("/api/workshops").json()[0]["id"] == setup.workshop.id
    assert all(row["class_id"] == setup.klass.id for row in client.get("/api/students").json())
    assert client.get("/api/users").status_code == 403
    assert client.get("/turmas").status_code == 403
    assert client.post(
        "/api/students",
        json={
            "name": "Cadastro proibido",
            "class_id": setup.klass.id,
            "enrollment_date": "2026-09-01",
        },
    ).status_code == 403
    own_report = client.post(
        "/api/reports/generate",
        json={
            "workshop_id": setup.workshop.id,
            "year": 2026,
            "month": 8,
            "use_ai": False,
        },
    )
    assert own_report.status_code == 200

    lesson = client.post(
        "/api/lessons",
        json={
            "class_id": setup.klass.id,
            "date": "2026-09-20",
            "summary": "Aula do professor",
            "attendance": [{"student_id": own_student.id, "status": "presente"}],
        },
    )
    assert lesson.status_code == 201
    assert client.post(
        "/api/lessons",
        json={
            "class_id": other_class.id,
            "date": "2026-09-20",
            "attendance": [{"student_id": other_student.id, "status": "presente"}],
        },
    ).status_code == 403
    assert client.post(
        "/api/attendance",
        json={
            "lesson_id": other_lesson.id,
            "records": [{"student_id": other_student.id, "status": "ausente"}],
        },
    ).status_code == 403

    with db.begin_nested():
        db.expire_all()
        saved = db.get(type(other_lesson), lesson.json()["id"])
        assert saved.created_by_user_id == professor.json()["id"]

    client.post("/logout")
    assert login(client, "admin@test.local", "senha-teste-123").status_code == 303
    assert client.get(f"/api/classes/{other_class.id}").status_code == 200
    assert client.get(f"/api/students/{other_student.id}").status_code == 200
    assert client.get(f"/api/lessons/{other_lesson.id}").status_code == 200
    assert client.get(f"/api/reports/{other_report['id']}").status_code == 200
