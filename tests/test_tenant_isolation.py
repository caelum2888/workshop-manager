"""v0.2 etapa 2: isolamento multi-tenant de leitura e acesso.

Duas organizações completas (Alfa e Beta), com volumes diferentes de dados. Cada
teste roda dos dois lados (Alfa vê Alfa, Beta vê Beta) e confere também o CORPO
das respostas: nada da outra organização pode aparecer.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.models import (
    Attendance,
    ClassGroup,
    Instructor,
    Lesson,
    MonthlyReport,
    Organization,
    Student,
    User,
    Workshop,
)
from app.services import attendance, auth, catalog, lessons, metrics, reports, students
from app.services.errors import InvalidData, NotFound
from tests.conftest import D

PASSWORD = "senha-segura-123"
GENERIC = "Recurso não encontrado."
MISSING = 999_999


# --------------------------------------------------------------------- mundo
def _user(name, email, role, org_id, **extra):
    return {"name": name, "email": email, "password": PASSWORD, "role": role, "organization_id": org_id, **extra}


def build_org(db, tag, main_students, main_lessons, extra_students, extra_lessons, all_present):
    """Organização completa: admin, professor, 2 oficinas (uma só é do professor), turmas, alunos, aulas, relatórios."""
    slug = tag.lower()
    org = Organization(name=f"Org {tag}", slug=slug)
    db.add(org)
    db.commit()
    oid = org.id
    admin = auth.create_user(db, _user(f"Admin {tag}", f"admin.{slug}@example.test", "admin", oid))
    main_w = catalog.create_workshop(db, {"name": f"Oficina {tag}"}, oid)
    extra_w = catalog.create_workshop(db, {"name": f"Oficina Extra {tag}"}, oid)  # NÃO é do professor
    prof = auth.create_user(
        db, _user(f"Prof {tag}", f"prof.{slug}@example.test", "professor", oid, workshop_ids=[main_w["id"]])
    )
    main_c = catalog.create_class(db, {"workshop_id": main_w["id"], "name": f"Turma {tag}"}, oid)
    extra_c = catalog.create_class(db, {"workshop_id": extra_w["id"], "name": f"Turma Extra {tag}"}, oid)

    def enroll(klass, prefix, n):
        return [
            students.create_student(
                db,
                {"name": f"{prefix} {tag} {i}", "class_id": klass["id"], "enrollment_date": D(2026, 7, 1), "status": "ativo"},
                oid,
            )
            for i in range(n)
        ]

    main_s, extra_s = enroll(main_c, "Aluno", main_students), enroll(extra_c, "Aluno Extra", extra_students)
    totals = {"records": 0, "present": 0}

    def teach(klass, roster, days):
        made = []
        for day in days:
            marks = [
                {"student_id": s["id"], "status": "presente" if all_present or i == 0 else "ausente"}
                for i, s in enumerate(roster)
            ]
            totals["records"] += len(marks)
            totals["present"] += sum(m["status"] == "presente" for m in marks)
            made.append(
                lessons.create_lesson(
                    db,
                    {"class_id": klass["id"], "date": day, "summary": f"Resumo {tag}", "attendance": marks},
                    organization_id=oid,
                )
            )
        return made

    main_l = teach(main_c, main_s, [D(2026, 9, 1), D(2026, 9, 8)][:main_lessons])
    extra_l = teach(extra_c, extra_s, [D(2026, 9, 2)][:extra_lessons])
    report = reports.generate_monthly_report(db, main_w["id"], 2026, 9, organization_id=oid)
    report_extra = reports.generate_monthly_report(db, extra_w["id"], 2026, 9, organization_id=oid)
    return SimpleNamespace(
        tag=tag, org_id=oid, admin_email=f"admin.{slug}@example.test", prof_email=f"prof.{slug}@example.test",
        u=prof["id"], admin_u=admin["id"], i=prof["instructor_id"],
        w=main_w["id"], xw=extra_w["id"], c=main_c["id"], xc=extra_c["id"],
        s=main_s[0]["id"], xs=extra_s[0]["id"], l=main_l[0]["id"], xl=extra_l[0]["id"],
        r=report["id"], xr=report_extra["id"],
        student_ids={s["id"] for s in main_s + extra_s}, lesson_ids={x["id"] for x in main_l + extra_l},
        class_ids={main_c["id"], extra_c["id"]}, workshop_ids={main_w["id"], extra_w["id"]},
        report_ids={report["id"], report_extra["id"]},
        enrolled=main_students + extra_students, lessons=main_lessons + extra_lessons,
        records=totals["records"], present=totals["present"],
        main_enrolled=main_students, main_lessons=main_lessons,
        main_student_ids={s["id"] for s in main_s}, main_lesson_ids={x["id"] for x in main_l},
    )


@pytest.fixture
def world(anonymous_client, session_factory):
    from app import main

    with session_factory() as db:
        alfa = build_org(db, "Alfa", main_students=2, main_lessons=1, extra_students=1, extra_lessons=1, all_present=True)
        beta = build_org(db, "Beta", main_students=3, main_lessons=2, extra_students=2, extra_lessons=1, all_present=False)

    def login(email):
        client = TestClient(main.app)
        response = client.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=False)
        assert response.status_code == 303, email
        return client

    return SimpleNamespace(alfa=alfa, beta=beta, login=login, session_factory=session_factory)


@pytest.fixture(params=["alfa", "beta"])
def sides(request, world):
    """(eu, outro, cliente admin de 'eu'): roda o teste dos dois lados."""
    me, other = (world.alfa, world.beta) if request.param == "alfa" else (world.beta, world.alfa)
    return me, other, world.login(me.admin_email)


@pytest.fixture(params=["alfa", "beta"])
def prof_sides(request, world):
    me, other = (world.alfa, world.beta) if request.param == "alfa" else (world.beta, world.alfa)
    return me, other, world.login(me.prof_email)


def assert_no_leak(response, other):
    text = response.text.lower()
    assert other.tag.lower() not in text, f"vazou {other.tag}: {response.request.url}"


def assert_hidden(response, other):
    """Recurso de outro tenant: 404 genérico, indistinguível de inexistente, sem vazamento.

    API (/api/*) continua JSON; página HTML (etapa 2) passa a ser uma tela amigável.
    """
    assert response.status_code == 404, (response.request.method, response.request.url, response.status_code, response.text)
    if response.request.url.path.startswith("/api/"):
        assert response.json()["detail"] == GENERIC, response.request.url
    else:
        assert "text/html" in response.headers.get("content-type", ""), response.request.url
    assert_no_leak(response, other)


def dump(session_factory):
    """Foto completa do banco (todas as colunas de todas as tabelas de negócio)."""
    with session_factory() as db:
        return {
            model.__name__: [
                {c.name: str(getattr(row, c.name)) for c in model.__table__.columns}
                for row in db.scalars(select(model).order_by(model.id))
            ]
            for model in (Organization, User, Instructor, Workshop, ClassGroup, Student, Lesson, Attendance, MonthlyReport)
        }


# ------------------------------------------------------- 1. listagens do admin
def test_admin_lists_only_own_tenant(sides):
    me, other, client = sides
    urls = [
        "/api/users", "/api/instructors", "/api/workshops", "/api/classes", "/api/students",
        "/api/lessons", "/api/reports", "/api/attendance/monthly?year=2026&month=9",
        "/api/metrics/monthly?year=2026&month=9", "/api/lessons?year=2026&month=9",
    ]
    bodies = {}
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        assert_no_leak(response, other)
        bodies[url] = response.json()

    assert {u["email"] for u in bodies["/api/users"]} == {me.admin_email, me.prof_email}
    assert {u["organization_id"] for u in bodies["/api/users"]} == {me.org_id}
    assert [i["id"] for i in bodies["/api/instructors"]] == [me.i]
    assert {w["id"] for w in bodies["/api/workshops"]} == me.workshop_ids
    assert {c["id"] for c in bodies["/api/classes"]} == me.class_ids
    assert {s["id"] for s in bodies["/api/students"]} == me.student_ids
    assert {x["id"] for x in bodies["/api/lessons"]} == me.lesson_ids
    assert {x["id"] for x in bodies["/api/lessons?year=2026&month=9"]} == me.lesson_ids
    assert {r["id"] for r in bodies["/api/reports"]} == me.report_ids
    grid = bodies["/api/attendance/monthly?year=2026&month=9"]
    assert {x["id"] for x in grid["lessons"]} == me.lesson_ids
    assert len(grid["students"]) == me.enrolled


def test_own_filters_still_work_for_admin(sides):
    me, _other, client = sides
    assert {s["id"] for s in client.get(f"/api/students?class_id={me.c}").json()} <= me.student_ids
    assert [w["id"] for w in client.get(f"/api/classes?workshop_id={me.w}").json()] == [me.c]
    assert client.get(f"/api/workshops/{me.w}").status_code == 200
    assert client.get(f"/api/reports/{me.r}").status_code == 200
    assert client.get(f"/relatorios/{me.r}/imprimir").status_code == 200


# ------------------------------------------------------------ 2. dashboard
def test_dashboard_counts_only_own_tenant(sides):
    me, other, client = sides
    response = client.get("/api/metrics/monthly?year=2026&month=9")
    assert_no_leak(response, other)
    data = response.json()
    assert data["enrolled_students"] == me.enrolled
    assert data["lessons_count"] == me.lessons
    assert data["total_attendance_records"] == me.records
    assert data["total_present"] == me.present
    assert {r["name"].split()[-2] for r in data["student_frequency"]} == {me.tag}

    by_workshop = client.get(f"/api/metrics/monthly?year=2026&month=9&workshop_id={me.w}").json()
    assert by_workshop["enrolled_students"] == me.main_enrolled
    assert by_workshop["lessons_count"] == me.main_lessons


def test_dashboards_of_the_two_tenants_never_mix(world):
    alfa = world.login(world.alfa.admin_email).get("/api/metrics/monthly?year=2026&month=9").json()
    beta = world.login(world.beta.admin_email).get("/api/metrics/monthly?year=2026&month=9").json()
    assert (alfa["enrolled_students"], alfa["lessons_count"]) == (3, 2)
    assert (beta["enrolled_students"], beta["lessons_count"]) == (5, 3)
    assert alfa["attendance_rate_percent"] == 100.0 and beta["attendance_rate_percent"] == 37.5


def test_reports_content_only_own_tenant(sides):
    me, other, client = sides
    for url in (f"/api/reports/{me.r}", f"/api/reports/monthly?workshop_id={me.w}&year=2026&month=9", "/api/reports"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert_no_leak(response, other)
    generated = client.post("/api/reports/generate", json={"workshop_id": me.w, "year": 2026, "month": 9})
    assert generated.status_code == 200 and generated.json()["id"] == me.r  # o do próprio tenant, não cria outro
    assert generated.json()["metrics"]["enrolled_students"] == me.main_enrolled


# ------------------------------------------- 3. acesso por id / parâmetros de outro tenant
def _read_urls(other):
    return [
        f"/api/workshops/{other.w}", f"/api/classes/{other.c}", f"/api/classes/{other.c}/roster?date=2026-09-01",
        f"/api/students/{other.s}", f"/api/students/{other.s}/frequency", f"/api/lessons/{other.l}",
        f"/api/reports/{other.r}", f"/api/reports/{other.r}/export.docx",
        f"/relatorios/{other.r}", f"/relatorios/{other.r}/imprimir",
        f"/api/reports/monthly?workshop_id={other.w}&year=2026&month=9",
        f"/api/students?class_id={other.c}", f"/api/students?workshop_id={other.w}",
        f"/api/classes?workshop_id={other.w}", f"/api/lessons?class_id={other.c}",
        f"/api/lessons?workshop_id={other.w}", f"/api/lessons?workshop_id={other.w}&year=2026&month=9",
        f"/api/attendance/monthly?workshop_id={other.w}&year=2026&month=9",
        f"/api/attendance/monthly?class_id={other.c}&year=2026&month=9",
        f"/api/metrics/monthly?workshop_id={other.w}", f"/api/metrics/monthly?class_id={other.c}",
        f"/api/reports?workshop_id={other.w}",
    ]


def test_admin_cannot_read_other_tenant_by_id_or_filter(sides):
    me, other, client = sides
    for url in _read_urls(other):
        assert_hidden(client.get(url), other)


def test_cross_tenant_is_indistinguishable_from_nonexistent(sides):
    me, other, client = sides
    ghost = SimpleNamespace(w=MISSING, c=MISSING, s=MISSING, l=MISSING, r=MISSING, tag="x")
    for foreign, missing in zip(_read_urls(other), _read_urls(ghost)):
        a, b = client.get(foreign), client.get(missing)
        if foreign.startswith("/api/"):
            assert (a.status_code, a.json()) == (b.status_code, b.json()), foreign
        else:
            assert (a.status_code, a.text) == (b.status_code, b.text), foreign


def _write_requests(me, other):
    return [
        ("PATCH", f"/api/instructors/{other.i}", {"name": "Hackeado"}),
        ("PATCH", f"/api/workshops/{other.w}", {"name": "Hackeado"}),
        ("PATCH", f"/api/classes/{other.c}", {"name": "Hackeado"}),
        ("PATCH", f"/api/students/{other.s}", {"name": "Hackeado"}),
        ("POST", f"/api/students/{other.s}/dropout", {"dropout_date": "2026-09-10"}),
        ("POST", f"/api/students/{other.s}/reactivate", None),
        ("PATCH", f"/api/lessons/{other.l}", {"summary": "Hackeado"}),
        ("DELETE", f"/api/lessons/{other.l}", None),
        ("POST", "/api/attendance", {"lesson_id": other.l, "records": []}),
        ("PATCH", f"/api/reports/{other.r}", {"activities_summary": "Hackeado"}),
        ("POST", f"/api/reports/{other.r}/suggestions", {"field": "activities_summary", "use_ai": False}),
        ("POST", f"/api/reports/{other.r}/finalize", None),
        ("POST", f"/api/reports/{other.r}/reopen", None),
        ("PATCH", f"/api/users/{other.u}", {"name": "Hackeado", "active": False}),
        ("PATCH", f"/api/users/{other.admin_u}", {"password": "outra-senha-123"}),
        ("POST", "/api/classes", {"workshop_id": other.w, "name": "Intrusa"}),
        ("POST", "/api/students", {"name": "Intruso", "class_id": other.c}),
        ("POST", "/api/lessons", {"class_id": other.c, "date": "2026-09-15", "summary": "x"}),
        ("POST", "/api/reports/generate", {"workshop_id": other.w, "year": 2026, "month": 10}),
        ("POST", "/api/workshops", {"name": "Nova", "instructor_id": other.i}),
        ("PATCH", f"/api/workshops/{me.w}", {"instructor_id": other.i}),  # meu recurso, referência alheia
        ("PATCH", f"/api/students/{me.s}", {"class_id": other.c}),
    ]


def test_admin_cannot_write_other_tenant_and_nothing_changes(world, sides):
    me, other, client = sides
    before = dump(world.session_factory)
    for method, url, body in _write_requests(me, other):
        response = client.request(method, url, json=body) if body is not None else client.request(method, url)
        assert_hidden(response, other)
    assert dump(world.session_factory) == before


def test_user_management_never_reaches_other_tenant(world, sides):
    me, other, client = sides
    before = dump(world.session_factory)
    foreign = {"workshop_ids": [other.w]}
    nonexistent = {"workshop_ids": [MISSING]}
    created = [
        client.post(
            "/api/users",
            json={"name": "Novo", "email": f"novo.{i}@example.test", "password": PASSWORD, "role": "professor", **body},
        )
        for i, body in enumerate((foreign, nonexistent))
    ]
    updated = [client.patch(f"/api/users/{me.u}", json=body) for body in (foreign, nonexistent)]
    for pair in (created, updated):
        assert pair[0].status_code == pair[1].status_code == 422
        assert pair[0].json()["detail"] == pair[1].json()["detail"]  # igual ao de id inexistente
        assert_no_leak(pair[0], other)
    assert dump(world.session_factory) == before  # nada foi criado nem atribuído


# --------------------------------------------------------------- 4. professor
def test_professor_lists_only_own_assignments(prof_sides):
    me, other, client = prof_sides
    for url in ("/api/workshops", "/api/classes", "/api/students", "/api/lessons", "/api/reports",
                "/api/attendance/monthly?year=2026&month=9"):
        assert_no_leak(client.get(url), other)
    assert [w["id"] for w in client.get("/api/workshops").json()] == [me.w]
    assert [c["id"] for c in client.get("/api/classes").json()] == [me.c]
    assert {s["id"] for s in client.get("/api/students").json()} == me.main_student_ids
    assert {x["id"] for x in client.get("/api/lessons").json()} == me.main_lesson_ids
    assert [r["id"] for r in client.get("/api/reports").json()] == [me.r]
    assert client.get("/api/users").status_code == 403
    assert client.get("/api/instructors").status_code == 403


def test_professor_dashboard_counts_only_assigned_data(prof_sides):
    me, other, client = prof_sides
    response = client.get("/api/metrics/monthly?year=2026&month=9")
    assert_no_leak(response, other)
    data = response.json()
    assert data["enrolled_students"] == me.main_enrolled
    assert data["lessons_count"] == me.main_lessons


def test_professor_same_tenant_unassigned_is_403_other_tenant_is_404(prof_sides):
    me, other, client = prof_sides
    unassigned = [
        f"/api/workshops/{me.xw}", f"/api/classes/{me.xc}", f"/api/students/{me.xs}", f"/api/lessons/{me.xl}",
        f"/api/reports/{me.xr}", f"/api/students?class_id={me.xc}", f"/api/lessons?workshop_id={me.xw}",
        f"/api/metrics/monthly?class_id={me.xc}", f"/relatorios/{me.xr}",
    ]
    for url in unassigned:
        assert client.get(url).status_code == 403, url
    for url in _read_urls(other):
        assert_hidden(client.get(url), other)


def test_professor_cannot_write_outside_scope(world, prof_sides):
    me, other, client = prof_sides
    before = dump(world.session_factory)
    for method, url, body in _write_requests(me, other):
        response = client.request(method, url, json=body) if body is not None else client.request(method, url)
        assert response.status_code in (403, 404), (url, response.status_code)
        assert_no_leak(response, other)
    # dentro da própria organização, mas fora das atribuições: 403
    assert client.post("/api/lessons", json={"class_id": me.xc, "date": "2026-09-15", "summary": "x"}).status_code == 403
    assert client.post("/api/reports/generate", json={"workshop_id": me.xw, "year": 2026, "month": 10}).status_code == 403
    assert client.post("/api/attendance", json={"lesson_id": me.xl, "records": []}).status_code == 403
    assert dump(world.session_factory) == before


# ----------------------------------------------------------------- 5. páginas
def test_html_pages_do_not_leak_other_tenant(sides):
    me, other, client = sides
    for url in ("/", "/aulas", "/aulas/registrar", "/dashboard", "/alunos", "/turmas", "/relatorios", "/usuarios",
                f"/relatorios/{me.r}", f"/relatorios/{me.r}/imprimir"):
        response = client.get(url)
        assert response.status_code == 200, url
        assert_no_leak(response, other)


def test_unauthenticated_behavior_is_unchanged(anonymous_client, world):
    assert anonymous_client.get("/api/students").status_code == 401
    assert anonymous_client.get(f"/api/reports/{world.alfa.r}").status_code == 401
    page = anonymous_client.get(f"/relatorios/{world.alfa.r}", follow_redirects=False)
    # etapa 2: preserva o destino em ?next= para voltar direto após o login
    assert (page.status_code, page.headers["location"]) == (303, f"/login?next=/relatorios/{world.alfa.r}")


# ------------------------------------------------------- 6. camada de services
def test_services_are_tenant_safe_without_the_routers(world):
    a, b = world.alfa, world.beta
    with world.session_factory() as db:
        oid = a.org_id
        assert students.list_students(db, class_id=b.c, organization_id=oid) == []
        assert students.list_students(db, workshop_id=b.w, organization_id=oid) == []
        assert lessons.list_lessons(db, class_id=b.c, organization_id=oid) == []
        assert lessons.list_lessons(db, workshop_id=b.w, organization_id=oid) == []
        assert catalog.list_classes(db, workshop_id=b.w, organization_id=oid) == []
        assert reports.list_reports(db, workshop_id=b.w, organization_id=oid) == []
        assert attendance.get_monthly_attendance(db, 2026, 9, class_id=b.c, organization_id=oid)["students"] == []
        assert attendance.enrolled_students(db, b.c, D(2026, 9, 1), oid) == []
        assert {x["id"] for x in lessons.list_lessons(db, organization_id=oid)} == a.lesson_ids
        for call in (
            lambda: metrics.get_monthly_metrics(db, 2026, 9, workshop_id=b.w, organization_id=oid),
            lambda: metrics.get_monthly_metrics(db, 2026, 9, class_id=b.c, organization_id=oid),
            lambda: students.get_student(db, b.s, organization_id=oid),
            lambda: attendance.get_student_frequency(db, b.s, organization_id=oid),
            lambda: lessons.get_lesson(db, b.l, organization_id=oid),
            lambda: lessons.get_roster(db, b.c, D(2026, 9, 1), organization_id=oid),
            lambda: reports.get_report(db, b.r, organization_id=oid),
            lambda: reports.build_monthly_report_data(db, b.w, 2026, 9, organization_id=oid),
            lambda: catalog.get_workshop(db, b.w, organization_id=oid),
            lambda: auth.get_user(db, b.u, organization_id=oid),
            lambda: attendance.record_attendance(db, b.l, [], organization_id=oid),
        ):
            with pytest.raises(NotFound, match=GENERIC):
                call()


def test_attendance_of_foreign_student_cannot_be_written_into_own_lesson(world):
    a, b = world.alfa, world.beta
    with world.session_factory() as db:
        before = len(list(db.scalars(select(Attendance))))
        own = [s for s in db.scalars(select(Student).where(Student.class_id == a.c))]
        records = [{"student_id": own[0].id, "status": "presente"}, {"student_id": b.s, "status": "presente"}]
        with pytest.raises(InvalidData):
            attendance.record_attendance(db, a.l, records, organization_id=a.org_id)
        assert len(list(db.scalars(select(Attendance)))) == before
        # o aluno de Beta continua só com a própria presença
        assert {x.lesson_id for x in db.scalars(select(Attendance).where(Attendance.student_id == b.s))} <= b.lesson_ids


def test_services_reject_relationship_injection_on_create(world):
    """Objetos ORM não podem contornar a validação dos ids tenant-aware."""
    a, b = world.alfa, world.beta
    before = dump(world.session_factory)
    with world.session_factory() as db:
        attacks = (
            lambda: catalog.create_workshop(
                db,
                {
                    "name": "Oficina injetada",
                    "instructor_id": a.i,
                    "instructor": db.get(Instructor, b.i),
                },
                a.org_id,
            ),
            lambda: catalog.create_class(
                db,
                {
                    "workshop_id": a.w,
                    "workshop": db.get(Workshop, b.w),
                    "name": "Turma injetada",
                },
                a.org_id,
            ),
            lambda: students.create_student(
                db,
                {
                    "name": "Aluno injetado",
                    "class_id": a.c,
                    "class_group": db.get(ClassGroup, b.c),
                    "enrollment_date": D(2026, 9, 1),
                    "status": "ativo",
                },
                a.org_id,
            ),
            lambda: lessons.create_lesson(
                db,
                {
                    "class_id": a.c,
                    "class_group": db.get(ClassGroup, b.c),
                    "date": D(2026, 10, 1),
                    "summary": "Aula injetada",
                },
                organization_id=a.org_id,
            ),
        )
        for attack in attacks:
            with pytest.raises(InvalidData, match="campos não são permitidos"):
                attack()
    assert dump(world.session_factory) == before


def test_services_reject_tenant_field_injection_on_update(world):
    """organization_id é contexto imutável, nunca um campo atualizável."""
    a, b = world.alfa, world.beta
    before = dump(world.session_factory)
    with world.session_factory() as db:
        attacks = (
            lambda: catalog.update_instructor(
                db, a.i, {"organization_id": b.org_id}, organization_id=a.org_id
            ),
            lambda: catalog.update_workshop(
                db, a.w, {"organization_id": b.org_id}, organization_id=a.org_id
            ),
            lambda: catalog.update_class(
                db, a.c, {"organization_id": b.org_id}, organization_id=a.org_id
            ),
            lambda: students.update_student(
                db, a.s, {"organization_id": b.org_id}, organization_id=a.org_id
            ),
            lambda: lessons.update_lesson(
                db, a.l, {"organization_id": b.org_id}, organization_id=a.org_id
            ),
        )
        for attack in attacks:
            with pytest.raises(InvalidData, match="campos não são permitidos"):
                attack()
    assert dump(world.session_factory) == before


def test_professor_without_instructor_fails_closed(world):
    """None não pode virar ausência de filtro (que significaria visão de admin)."""
    from app.services.errors import Forbidden

    a = world.alfa
    with world.session_factory() as db:
        db.get(User, a.u).instructor_id = None
        db.commit()
    client = world.login(a.prof_email)
    for url in (
        "/", "/api/workshops", "/api/classes", "/api/students", "/api/lessons",
        "/api/reports", "/api/attendance/monthly?year=2026&month=9",
        "/api/metrics/monthly?year=2026&month=9",
    ):
        response = client.get(url)
        assert response.status_code == 403, (url, response.status_code, response.text)
    with world.session_factory() as db:
        with pytest.raises(Forbidden):
            from app.services.access import professor_instructor_id

            professor_instructor_id(db.get(User, a.u))


def test_every_tenant_owned_model_has_organization_column():
    """Guarda contra esquecimento: todo modelo de negócio (exceto Attendance, que herda da aula) é escopável."""
    for model in (User, Instructor, Workshop, ClassGroup, Student, Lesson, MonthlyReport):
        assert hasattr(model, "organization_id"), model.__name__
    assert not hasattr(Attendance, "organization_id")
