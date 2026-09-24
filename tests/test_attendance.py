import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.models import Attendance, Lesson
from app.services import lessons, students
from app.services.attendance import record_attendance
from app.services.errors import Conflict, InvalidData
from tests.conftest import D, add_lesson, add_student


def count_attendance(db, lesson_id):
    return db.scalar(select(func.count()).select_from(Attendance).where(Attendance.lesson_id == lesson_id))


def test_recording_twice_updates_instead_of_duplicating(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    lesson = lessons.create_lesson(
        db, {"class_id": setup.klass.id, "date": D(2026, 9, 1), "attendance": [{"student_id": ana.id, "status": "presente"}]},
        organization_id=setup.org_id,
    )
    result = record_attendance(db, lesson["id"], [{"student_id": ana.id, "status": "ausente"}], organization_id=setup.org_id)

    assert count_attendance(db, lesson["id"]) == 1
    assert result["attendance"][0]["status"] == "ausente"


def test_database_rejects_duplicate_attendance(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    lesson = add_lesson(db, setup.klass, D(2026, 9, 1), {ana: "presente"})
    db.add(Attendance(lesson_id=lesson.id, student_id=ana.id, status="ausente"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_same_student_twice_in_payload_is_rejected(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    with pytest.raises(InvalidData):
        lessons.create_lesson(
            db,
            {
                "class_id": setup.klass.id,
                "date": D(2026, 9, 1),
                "attendance": [{"student_id": ana.id, "status": "presente"}, {"student_id": ana.id, "status": "ausente"}],
            },
            organization_id=setup.org_id,
        )
    assert lessons.list_lessons(db, organization_id=setup.org_id) == []  # nada gravado pela metade


def test_lesson_cannot_be_saved_with_incomplete_attendance(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    bia = add_student(db, setup.klass, "Bia", D(2026, 7, 1))

    with pytest.raises(InvalidData) as exc:
        lessons.create_lesson(
            db,
            {
                "class_id": setup.klass.id,
                "date": D(2026, 9, 1),
                "summary": "Aula incompleta",
                "attendance": [{"student_id": ana.id, "status": "presente"}],
            },
            organization_id=setup.org_id,
        )

    assert exc.value.details["missing_count"] == 1
    assert exc.value.details["missing_students"] == [{"student_id": bia.id, "name": "Bia"}]
    assert db.scalar(select(func.count()).select_from(Lesson)) == 0


def test_editing_text_fields_does_not_reopen_completeness_after_retroactive_enrollment(db, setup):
    """Achado na auditoria de pré-release: matricular um aluno com data retroativa (comum ao
    importar planilha com histórico real) travava a edição de QUALQUER aula antiga já salva com
    chamada completa — mesmo editando só o resumo, sem tocar em presença ou data."""
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    lesson = lessons.create_lesson(
        db,
        {"class_id": setup.klass.id, "date": D(2026, 9, 1), "summary": "Original",
         "attendance": [{"student_id": ana.id, "status": "presente"}]},
        organization_id=setup.org_id,
    )
    # matrícula retroativa: antes da data da aula já salva
    add_student(db, setup.klass, "Bia", D(2026, 7, 1))

    updated = lessons.update_lesson(db, lesson["id"], {"summary": "Corrigida"}, organization_id=setup.org_id)
    assert updated["summary"] == "Corrigida"

    # mudar a DATA continua exigindo chamada completa para a nova data (comportamento original preservado)
    with pytest.raises(InvalidData):
        lessons.update_lesson(db, lesson["id"], {"date": D(2026, 9, 2)}, organization_id=setup.org_id)


def test_all_present_payload_marks_every_student_and_allows_later_change(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    bia = add_student(db, setup.klass, "Bia", D(2026, 7, 1))
    lesson = lessons.create_lesson(
        db,
        {
            "class_id": setup.klass.id,
            "date": D(2026, 9, 1),
            "attendance": [
                {"student_id": ana.id, "status": "presente"},
                {"student_id": bia.id, "status": "presente"},
            ],
        },
        organization_id=setup.org_id,
    )
    changed = record_attendance(
        db,
        lesson["id"],
        [
            {"student_id": ana.id, "status": "presente"},
            {"student_id": bia.id, "status": "atestado"},
        ],
        organization_id=setup.org_id,
    )
    assert changed["attendance_counts"] == {
        "present": 1,
        "absent": 0,
        "excused": 1,
        "total": 2,
    }


def test_changing_lesson_date_requires_reconciled_roster(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    bia = add_student(db, setup.klass, "Bia", D(2026, 9, 10))
    lesson = lessons.create_lesson(
        db,
        {
            "class_id": setup.klass.id,
            "date": D(2026, 9, 5),
            "attendance": [{"student_id": ana.id, "status": "presente"}],
        },
        organization_id=setup.org_id,
    )

    with pytest.raises(InvalidData):
        lessons.update_lesson(db, lesson["id"], {"date": D(2026, 9, 15)}, organization_id=setup.org_id)

    updated = lessons.update_lesson(
        db,
        lesson["id"],
        {
            "date": D(2026, 9, 15),
            "attendance": [
                {"student_id": ana.id, "status": "presente"},
                {"student_id": bia.id, "status": "ausente"},
            ],
        },
        organization_id=setup.org_id,
    )
    assert updated["date"] == D(2026, 9, 15)
    assert updated["attendance_counts"]["total"] == 2


def test_student_from_another_class_is_rejected(db, setup):
    caio = add_student(db, setup.other, "Caio", D(2026, 7, 1))
    lesson = add_lesson(db, setup.klass, D(2026, 9, 1))
    with pytest.raises(InvalidData):
        record_attendance(db, lesson.id, [{"student_id": caio.id, "status": "presente"}], organization_id=setup.org_id)


def test_student_not_enrolled_on_lesson_date_is_rejected(db, setup):
    late = add_student(db, setup.klass, "Matrícula tardia", D(2026, 9, 10))
    lesson = add_lesson(db, setup.klass, D(2026, 9, 1))
    with pytest.raises(InvalidData):
        record_attendance(db, lesson.id, [{"student_id": late.id, "status": "presente"}], organization_id=setup.org_id)


def test_one_lesson_per_class_per_day(db, setup):
    lessons.create_lesson(db, {"class_id": setup.klass.id, "date": D(2026, 9, 1), "summary": "a"}, organization_id=setup.org_id)
    with pytest.raises(Conflict) as exc:
        lessons.create_lesson(db, {"class_id": setup.klass.id, "date": D(2026, 9, 1), "summary": "b"}, organization_id=setup.org_id)
    assert "lesson_id" in exc.value.details
    # outra turma no mesmo dia é permitido
    lessons.create_lesson(db, {"class_id": setup.other.id, "date": D(2026, 9, 1), "summary": "c"}, organization_id=setup.org_id)


def test_roster_lists_only_students_enrolled_on_date(db, setup):
    add_student(db, setup.klass, "Ativa", D(2026, 7, 1))
    add_student(db, setup.klass, "Desistiu antes", D(2026, 7, 1), dropout=D(2026, 8, 30))
    add_student(db, setup.klass, "Matricula depois", D(2026, 9, 20))

    roster = lessons.get_roster(db, setup.klass.id, D(2026, 9, 1), organization_id=setup.org_id)
    assert [s["name"] for s in roster["students"]] == ["Ativa"]
    assert roster["lesson"] is None


def test_dropout_requires_consistent_dates(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 9, 1))
    with pytest.raises(InvalidData):
        students.register_dropout(db, ana.id, D(2026, 8, 1), organization_id=setup.org_id)
    result = students.register_dropout(db, ana.id, D(2026, 9, 15), organization_id=setup.org_id)
    assert result["status"] == "desistente"
    assert students.reactivate_student(db, ana.id, organization_id=setup.org_id)["dropout_date"] is None


def test_api_lesson_flow(client, setup, db):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    body = {
        "class_id": setup.klass.id,
        "date": "2026-09-01",
        "summary": "Perspectiva",
        "occurrences": "  ",
        "attendance": [{"student_id": ana.id, "status": "presente"}],
    }
    created = client.post("/api/lessons", json=body)
    assert created.status_code == 201
    assert created.json()["occurrences"] is None
    assert created.json()["attendance_counts"]["present"] == 1

    duplicate = client.post("/api/lessons", json=body)
    assert duplicate.status_code == 409
    assert duplicate.json()["details"]["lesson_id"] == created.json()["id"]

    invalid_status = client.post("/api/attendance", json={"lesson_id": created.json()["id"], "records": [{"student_id": ana.id, "status": "talvez"}]})
    assert invalid_status.status_code == 422


def test_api_rejects_incomplete_attendance_with_actionable_details(client, setup, db):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    bia = add_student(db, setup.klass, "Bia", D(2026, 7, 1))
    response = client.post(
        "/api/lessons",
        json={
            "class_id": setup.klass.id,
            "date": "2026-09-01",
            "summary": "Teste",
            "attendance": [{"student_id": ana.id, "status": "presente"}],
        },
    )
    assert response.status_code == 422
    assert response.json()["details"]["missing_students"] == [
        {"student_id": bia.id, "name": "Bia"}
    ]
