import pytest

from app.models import Attendance
from app.services import lessons
from app.services.attendance import frequency_percent, get_student_frequency
from app.services.errors import InvalidData
from app.services.metrics import get_monthly_metrics
from app.services.periods import month_bounds
from tests.conftest import D, add_lesson, add_student


def test_frequency_percent():
    assert frequency_percent(3, 4) == 75.0
    assert frequency_percent(2, 3) == 66.7
    assert frequency_percent(0, 5) == 0.0
    assert frequency_percent(0, 0) is None


def test_month_bounds_handles_short_months():
    assert month_bounds(2026, 2) == (D(2026, 2, 1), D(2026, 2, 28))
    assert month_bounds(2028, 2) == (D(2028, 2, 1), D(2028, 2, 29))
    assert month_bounds(2026, 12) == (D(2026, 12, 1), D(2026, 12, 31))


def test_attendance_average_and_totals(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    bia = add_student(db, setup.klass, "Bia", D(2026, 7, 1))
    add_lesson(db, setup.klass, D(2026, 9, 1), {ana: "presente", bia: "ausente"})
    add_lesson(db, setup.klass, D(2026, 9, 3), {ana: "presente", bia: "atestado"})

    m = get_monthly_metrics(db, 2026, 9, workshop_id=setup.workshop.id, organization_id=setup.org_id)

    assert m["lessons_count"] == 2
    assert (m["total_present"], m["total_absent"], m["total_excused"]) == (2, 1, 1)
    assert m["total_attendance_records"] == 4
    assert m["attendance_rate_percent"] == 50.0
    assert m["average_present_per_lesson"] == 1.0
    freq = {r["name"]: r["frequency_percent"] for r in m["student_frequency"]}
    assert freq == {"Ana": 100.0, "Bia": 0.0}
    assert m["lowest_frequency"][0]["name"] == "Bia"
    assert m["frequency_rule"]["unmarked_policy"] == "sem marcação não é convertido em ausência"


def test_student_frequency_present_absent_and_excused(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    add_lesson(db, setup.klass, D(2026, 9, 1), {ana: "presente"})
    add_lesson(db, setup.klass, D(2026, 9, 2), {ana: "ausente"})
    add_lesson(db, setup.klass, D(2026, 9, 3), {ana: "atestado"})

    frequency = get_student_frequency(db, ana.id, 2026, 9, organization_id=setup.org_id)
    assert frequency["eligible_lessons"] == 3
    assert (frequency["present"], frequency["absent"], frequency["excused"]) == (1, 1, 1)
    assert frequency["frequency_percent"] == 33.3


def test_frequency_respects_mid_month_enrollment(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 9, 8))
    before = add_lesson(db, setup.klass, D(2026, 9, 5))
    # Simula dado legado inconsistente para provar que o cálculo não o aceita.
    db.add(Attendance(lesson=before, student=ana, status="ausente"))
    add_lesson(db, setup.klass, D(2026, 9, 10), {ana: "presente"})
    add_lesson(db, setup.klass, D(2026, 9, 15), {ana: "presente"})
    db.commit()

    frequency = get_student_frequency(db, ana.id, 2026, 9, organization_id=setup.org_id)
    assert frequency["eligible_lessons"] == 2
    assert frequency["lessons_recorded"] == 2
    assert frequency["absent"] == 0
    assert frequency["frequency_percent"] == 100.0


def test_frequency_respects_mid_month_dropout(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 9, 1), dropout=D(2026, 9, 12))
    add_lesson(db, setup.klass, D(2026, 9, 5), {ana: "presente"})
    add_lesson(db, setup.klass, D(2026, 9, 10), {ana: "ausente"})
    after = add_lesson(db, setup.klass, D(2026, 9, 15))
    db.add(Attendance(lesson=after, student=ana, status="ausente"))
    db.commit()

    frequency = get_student_frequency(db, ana.id, 2026, 9, organization_id=setup.org_id)
    assert frequency["eligible_lessons"] == 2
    assert frequency["lessons_recorded"] == 2
    assert frequency["absent"] == 1
    assert frequency["frequency_percent"] == 50.0


def test_monthly_filter_ignores_other_months(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    add_lesson(db, setup.klass, D(2026, 8, 31), {ana: "ausente"})
    add_lesson(db, setup.klass, D(2026, 9, 1), {ana: "presente"})
    add_lesson(db, setup.klass, D(2026, 9, 30), {ana: "presente"})
    add_lesson(db, setup.klass, D(2026, 10, 1), {ana: "ausente"})

    m = get_monthly_metrics(db, 2026, 9, organization_id=setup.org_id)

    assert m["lessons_count"] == 2
    assert m["total_absent"] == 0
    assert m["attendance_rate_percent"] == 100.0


def test_no_lessons_gives_empty_indicators(db, setup):
    add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    m = get_monthly_metrics(db, 2026, 9, organization_id=setup.org_id)
    assert m["lessons_count"] == 0
    assert m["attendance_rate_percent"] is None
    assert m["average_present_per_lesson"] is None
    assert m["enrolled_students"] == 1


def test_empty_class_and_month_without_lessons_are_safe(db, setup):
    empty_lesson = lessons.create_lesson(
        db, {"class_id": setup.klass.id, "date": D(2026, 9, 1), "attendance": []},
        organization_id=setup.org_id,
    )
    assert empty_lesson["attendance_counts"]["total"] == 0
    m = get_monthly_metrics(db, 2026, 9, class_id=setup.klass.id, organization_id=setup.org_id)
    assert m["lessons_count"] == 1
    assert m["enrolled_students"] == 0
    assert m["attendance_rate_percent"] is None
    assert m["average_present_per_lesson"] == 0.0

    october = get_monthly_metrics(db, 2026, 10, class_id=setup.klass.id, organization_id=setup.org_id)
    assert october["lessons_count"] == 0
    assert october["attendance_rate_percent"] is None
    assert october["average_present_per_lesson"] is None


def test_delete_lesson_recalculates_metrics_and_cascades_attendance(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    first = add_lesson(db, setup.klass, D(2026, 9, 1), {ana: "presente"})
    attendance_id = first.attendances[0].id
    add_lesson(db, setup.klass, D(2026, 9, 2), {ana: "ausente"})
    assert get_monthly_metrics(db, 2026, 9, organization_id=setup.org_id)["attendance_rate_percent"] == 50.0

    result = lessons.delete_lesson(db, first.id, organization_id=setup.org_id)
    assert result["attendance_deleted"] == 1
    assert db.get(Attendance, attendance_id) is None
    recalculated = get_monthly_metrics(db, 2026, 9, organization_id=setup.org_id)
    assert recalculated["lessons_count"] == 1
    assert recalculated["attendance_rate_percent"] == 0.0


def test_new_enrollments_dropouts_and_active(db, setup):
    add_student(db, setup.klass, "A (ago)", D(2026, 8, 15))
    add_student(db, setup.klass, "B (set)", D(2026, 9, 3))
    add_student(db, setup.klass, "C (desiste set)", D(2026, 7, 1), dropout=D(2026, 9, 10))
    add_student(db, setup.klass, "D (desiste ago)", D(2026, 7, 1), dropout=D(2026, 8, 20))
    add_student(db, setup.klass, "E (out)", D(2026, 10, 1))

    sep = get_monthly_metrics(db, 2026, 9, organization_id=setup.org_id)
    assert sep["enrolled_students"] == 3  # A, B, C
    assert sep["active_students"] == 2  # A, B
    assert sep["new_enrollments"] == 1
    assert [s["name"] for s in sep["new_enrollment_list"]] == ["B (set)"]
    assert sep["dropouts"] == 1
    assert [s["name"] for s in sep["dropout_list"]] == ["C (desiste set)"]

    aug = get_monthly_metrics(db, 2026, 8, organization_id=setup.org_id)
    assert aug["enrolled_students"] == 3  # A, C, D
    assert aug["active_students"] == 2  # A, C
    assert aug["new_enrollments"] == 1
    assert aug["dropouts"] == 1


def test_class_scope_only_counts_that_class(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    caio = add_student(db, setup.other, "Caio", D(2026, 7, 1))
    add_lesson(db, setup.klass, D(2026, 9, 1), {ana: "presente"})
    add_lesson(db, setup.other, D(2026, 9, 1), {caio: "ausente"})

    m = get_monthly_metrics(db, 2026, 9, class_id=setup.klass.id, organization_id=setup.org_id)
    assert m["lessons_count"] == 1
    assert m["enrolled_students"] == 1
    assert m["attendance_rate_percent"] == 100.0
    assert m["scope"]["workshop_id"] == setup.workshop.id

    whole = get_monthly_metrics(db, 2026, 9, workshop_id=setup.workshop.id, organization_id=setup.org_id)
    assert whole["lessons_count"] == 2
    assert whole["attendance_rate_percent"] == 50.0


def test_class_outside_workshop_is_rejected(db, setup):
    from app.models import Workshop, ClassGroup

    oid = setup.workshop.organization_id
    other_workshop = Workshop(organization_id=oid, name="Violão")
    foreign = ClassGroup(organization_id=oid, workshop=other_workshop, name="Violão — 10h")
    db.add_all([other_workshop, foreign])
    db.commit()
    with pytest.raises(InvalidData):
        get_monthly_metrics(db, 2026, 9, workshop_id=setup.workshop.id, class_id=foreign.id, organization_id=setup.org_id)
