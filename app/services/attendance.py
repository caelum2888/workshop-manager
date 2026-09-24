"""Presença: gravação completa, elegibilidade e cálculo de frequência.

Regra central de frequência (preservada do MVP original):

``presenças / registros válidos de presença * 100``

Ausência e atestado entram no denominador e não no numerador. Atestado não é
convertido em falta: ele continua auditável em uma contagem separada. Só são
válidos registros de aulas da turma do aluno ocorridas a partir da matrícula e
antes da data de desistência. Uma marcação ausente nunca é inferida como falta.
"""

import datetime as dt
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ATTENDANCE_STATUSES, Attendance, ClassGroup, Lesson, Student
from app.services.errors import InvalidData
from app.services.organizations import get_for_org
from app.services.periods import month_bounds, period_dict


def frequency_percent(present: int, total: int) -> float | None:
    """Percentual de presença (presentes / registros). None se não há registros."""
    if total <= 0:
        return None
    return round(present * 100 / total, 1)


def calculate_frequency(statuses: list[str]) -> dict:
    """Calcula uma frequência auditável a partir de marcações já validadas.

    Esta é a única fórmula usada por frequência individual, dashboard e
    relatório. ``atestado`` participa do denominador, mas permanece separado de
    ``ausente``. Lista vazia resulta em percentual ``None`` (não 0%).
    """
    counts = Counter(statuses)
    total = sum(counts[status] for status in ATTENDANCE_STATUSES)
    return {
        "lessons_recorded": total,
        "present": counts["presente"],
        "absent": counts["ausente"],
        "excused": counts["atestado"],
        "frequency_percent": frequency_percent(counts["presente"], total),
    }


def _validate_complete_roster(db: Session, lesson: Lesson, records: list[dict]) -> None:
    """Exige exatamente uma marcação para cada aluno matriculado na data."""
    ids = [record["student_id"] for record in records]
    duplicated = sorted(student_id for student_id, count in Counter(ids).items() if count > 1)
    if duplicated:
        raise InvalidData(
            "Aluno(s) repetido(s) na mesma lista de presença.",
            {"student_ids": duplicated},
        )

    roster = {
        student.id: student
        for student in enrolled_students(db, lesson.class_id, lesson.date, lesson.organization_id)
    }
    received = set(ids)
    missing = [roster[student_id] for student_id in sorted(roster.keys() - received)]
    unexpected = sorted(received - roster.keys())
    if missing or unexpected:
        details = {
            "missing_count": len(missing),
            "missing_students": [
                {"student_id": student.id, "name": student.name} for student in missing
            ],
            "unexpected_student_ids": unexpected,
        }
        if missing:
            raise InvalidData(
                f"A chamada está incompleta: faltam marcar {len(missing)} aluno(s).",
                details,
            )
        raise InvalidData("A chamada contém aluno(s) que não estavam matriculados na data.", details)


def upsert_attendance(
    db: Session, lesson: Lesson, records: list[dict], *, require_complete: bool = False
) -> None:
    """Grava presenças de uma aula. Não faz commit.

    - Um registro por aluno por aula: se já existir, atualiza o status.
    - O aluno precisa ser da turma da aula e estar matriculado na data.
    - Com ``require_complete``, a lista substitui a chamada inteira e precisa
      conter exatamente todos os alunos matriculados na data.
    """
    student_ids = [r["student_id"] for r in records]
    if require_complete:
        _validate_complete_roster(db, lesson, records)
    else:
        duplicated = sorted(
            student_id for student_id, count in Counter(student_ids).items() if count > 1
        )
        if duplicated:
            raise InvalidData(
                "Aluno(s) repetido(s) na mesma lista de presença.",
                {"student_ids": duplicated},
            )

    students = {
        s.id: s
        for s in db.scalars(
            select(Student).where(Student.id.in_(student_ids), Student.organization_id == lesson.organization_id)
        )
    }
    existing = {a.student_id: a for a in lesson.attendances}

    for record in records:
        student = students.get(record["student_id"])
        if student is None:
            raise InvalidData(f"Aluno(a) {record['student_id']} não encontrado(a).")
        if student.class_id != lesson.class_id:
            raise InvalidData(f"{student.name} não pertence à turma desta aula.")
        if record["student_id"] not in existing and not student.is_enrolled_on(lesson.date):
            raise InvalidData(
                f"{student.name} não estava matriculado(a) em {lesson.date:%d/%m/%Y}."
            )
        if record["status"] not in ATTENDANCE_STATUSES:
            raise InvalidData(f"Status de presença inválido: {record['status']}.")

        current = existing.get(student.id)
        if current is not None:
            current.status = record["status"]
        else:
            lesson.attendances.append(Attendance(student_id=student.id, status=record["status"]))

    if require_complete:
        received = set(student_ids)
        for attendance in list(lesson.attendances):
            if attendance.student_id not in received:
                lesson.attendances.remove(attendance)


def record_attendance(
    db: Session, lesson_id: int, records: list[dict], user_id: int | None = None, *, organization_id: int
) -> dict:
    """Registra/atualiza a presença de uma aula existente (sempre via a aula, dentro da organização)."""
    from app.services.lessons import lesson_to_dict

    lesson = get_for_org(db, Lesson, lesson_id, organization_id)
    try:
        upsert_attendance(db, lesson, records, require_complete=True)
        lesson.updated_by_user_id = user_id
        db.commit()
    except Exception:
        db.rollback()
        raise
    return lesson_to_dict(lesson)


def register_attendance(
    db: Session, lesson_id: int, records: list[dict], user_id: int | None = None, *, organization_id: int
) -> dict:
    """Nome estável para integrações futuras; registra a chamada completa."""
    return record_attendance(db, lesson_id, records, user_id, organization_id=organization_id)


def get_monthly_attendance(
    db: Session,
    year: int,
    month: int,
    workshop_id: int | None = None,
    class_id: int | None = None,
    instructor_id: int | None = None,
    *,
    organization_id: int,
) -> dict:
    """Grade de presença do mês: aulas (colunas) x alunos (linhas). Presença só entra via Lesson."""
    first, last = month_bounds(year, month)
    lesson_stmt = (
        select(Lesson)
        .join(ClassGroup)
        .where(
            Lesson.organization_id == organization_id,
            ClassGroup.organization_id == organization_id,
            Lesson.date >= first,
            Lesson.date <= last,
        )
        .order_by(Lesson.date, ClassGroup.name)
    )
    if workshop_id is not None:
        lesson_stmt = lesson_stmt.where(ClassGroup.workshop_id == workshop_id)
    if class_id is not None:
        lesson_stmt = lesson_stmt.where(Lesson.class_id == class_id)
    if instructor_id is not None:
        lesson_stmt = lesson_stmt.where(ClassGroup.workshop.has(instructor_id=instructor_id))
    lessons = list(db.scalars(lesson_stmt))

    rows: dict[int, dict] = {}
    for lesson in lessons:
        for att in lesson.attendances:
            row = rows.setdefault(
                att.student_id,
                {
                    "student_id": att.student_id,
                    "name": att.student.name,
                    "class_name": att.student.class_group.name,
                    "records": {},
                },
            )
            row["records"][lesson.id] = att.status

    return {
        "period": period_dict(year, month),
        "lessons": [
            {"id": l.id, "date": l.date, "class_id": l.class_id, "class_name": l.class_group.name}
            for l in lessons
        ],
        "students": sorted(rows.values(), key=lambda r: r["name"]),
    }


def get_student_frequency(
    db: Session, student_id: int, year: int | None = None, month: int | None = None, *, organization_id: int
) -> dict:
    """Frequência de um aluno (no mês informado ou em todo o histórico)."""
    student = get_for_org(db, Student, student_id, organization_id)
    period = None
    first = last = None
    if year and month:
        first, last = month_bounds(year, month)
        period = period_dict(year, month)
    calculation = calculate_student_frequency(db, student, first=first, last=last)
    return {
        "student_id": student.id,
        "name": student.name,
        "class_name": student.class_group.name,
        "status": student.status,
        "period": period,
        **calculation,
    }


def calculate_student_frequency(
    db: Session,
    student: Student,
    *,
    first: dt.date | None = None,
    last: dt.date | None = None,
) -> dict:
    """Calcula a frequência do aluno somente sobre suas aulas elegíveis.

    Uma aula é elegível se pertence à turma do aluno e sua data está no
    intervalo [matrícula, desistência). Marcações faltantes são expostas em
    ``unmarked_lessons`` e não são transformadas silenciosamente em ausências.
    """
    stmt = (
        select(Lesson)
        .where(Lesson.class_id == student.class_id, Lesson.organization_id == student.organization_id)
        .order_by(Lesson.date)
    )
    if first is not None:
        stmt = stmt.where(Lesson.date >= first)
    if last is not None:
        stmt = stmt.where(Lesson.date <= last)
    eligible_lessons = [lesson for lesson in db.scalars(stmt) if student.is_enrolled_on(lesson.date)]
    lesson_ids = [lesson.id for lesson in eligible_lessons]
    attendances = {
        attendance.lesson_id: attendance
        for attendance in db.scalars(
            select(Attendance).where(
                Attendance.student_id == student.id,
                Attendance.lesson_id.in_(lesson_ids),
            )
        )
    } if lesson_ids else {}
    records = [
        {
            "lesson_id": lesson.id,
            "date": lesson.date,
            "status": attendances[lesson.id].status,
        }
        for lesson in eligible_lessons
        if lesson.id in attendances
    ]
    result = calculate_frequency([record["status"] for record in records])
    result.update(
        {
            "eligible_lessons": len(eligible_lessons),
            "unmarked_lessons": len(eligible_lessons) - len(records),
            "records": records,
        }
    )
    return result


def enrolled_students(db: Session, class_id: int, day: dt.date, organization_id: int) -> list[Student]:
    """Alunos da turma matriculados na data (base da chamada)."""
    students = db.scalars(
        select(Student)
        .where(Student.class_id == class_id, Student.organization_id == organization_id)
        .order_by(Student.name)
    )
    return [s for s in students if s.is_enrolled_on(day)]
