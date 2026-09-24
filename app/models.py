"""Modelos do banco.

Nomes de tabelas/campos em inglês (API estável para integrações);
valores de status em português, como usados pela equipe.
"""

import datetime as dt

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

STUDENT_STATUSES = ("ativo", "desistente")
ATTENDANCE_STATUSES = ("presente", "ausente", "atestado")
USER_ROLES = ("admin", "professor")
REPORT_STATUSES = ("rascunho", "final")
METHODOLOGY_RATINGS = ("excelente", "bom", "regular", "atencao")
INFRASTRUCTURE_OPTIONS = ("adequada", "inadequada")
MATERIALS_OPTIONS = ("suficientes", "faltando")


def _organization_fk(table: str) -> Mapped[int]:
    """FK obrigatória e indexada para a organização dona do registro.

    Nome explícito, igual ao da migration, para create_all e Alembic coincidirem.
    """
    return mapped_column(
        ForeignKey("organizations.id", name=f"fk_{table}_organization_id_organizations"),
        index=True,
    )


class Organization(Base):
    """Operação/cliente dona dos dados (base para multi-organização)."""

    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug", name="uq_organization_slug"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())


class User(Base):
    """Conta autenticável; professores vinculam-se 1:1 a um oficineiro."""

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("email", name="uq_user_email"),
        UniqueConstraint("instructor_id", name="uq_user_instructor"),
        CheckConstraint("role IN ('admin', 'professor')", name="ck_user_role"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = _organization_fk("users")
    name: Mapped[str] = mapped_column(String(160))
    email: Mapped[str] = mapped_column(String(200), index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    instructor_id: Mapped[int | None] = mapped_column(
        ForeignKey("instructors.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())

    organization: Mapped[Organization] = relationship(lazy="joined")
    instructor: Mapped["Instructor | None"] = relationship(back_populates="user")

    @property
    def can_authenticate(self) -> bool:
        return self.active and self.organization.active


class Instructor(Base):
    """Oficineiro(a)."""

    __tablename__ = "instructors"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = _organization_fk("instructors")
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    workshops: Mapped[list["Workshop"]] = relationship(back_populates="instructor")
    user: Mapped[User | None] = relationship(back_populates="instructor", uselist=False)


class Workshop(Base):
    """Oficina (ex.: Desenho, linguagem Artes Visuais, na comunidade X)."""

    __tablename__ = "workshops"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = _organization_fk("workshops")
    name: Mapped[str] = mapped_column(String(120))
    language: Mapped[str] = mapped_column(String(120), default="")
    location: Mapped[str] = mapped_column(String(200), default="")
    instructor_id: Mapped[int | None] = mapped_column(ForeignKey("instructors.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    instructor: Mapped[Instructor | None] = relationship(back_populates="workshops")
    classes: Mapped[list["ClassGroup"]] = relationship(
        back_populates="workshop", order_by="ClassGroup.name"
    )


class ClassGroup(Base):
    """Turma de uma oficina."""

    __tablename__ = "class_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = _organization_fk("class_groups")
    workshop_id: Mapped[int] = mapped_column(ForeignKey("workshops.id"))
    name: Mapped[str] = mapped_column(String(120))
    schedule: Mapped[str | None] = mapped_column(String(120))
    location: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    workshop: Mapped[Workshop] = relationship(back_populates="classes")
    students: Mapped[list["Student"]] = relationship(
        back_populates="class_group", order_by="Student.name"
    )
    lessons: Mapped[list["Lesson"]] = relationship(back_populates="class_group")


class Student(Base):
    __tablename__ = "students"
    __table_args__ = (
        CheckConstraint("status IN ('ativo', 'desistente')", name="ck_student_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = _organization_fk("students")
    name: Mapped[str] = mapped_column(String(160))
    class_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id"))
    status: Mapped[str] = mapped_column(String(20), default="ativo")
    enrollment_date: Mapped[dt.date] = mapped_column(Date)
    dropout_date: Mapped[dt.date | None] = mapped_column(Date)

    class_group: Mapped[ClassGroup] = relationship(back_populates="students")
    attendances: Mapped[list["Attendance"]] = relationship(back_populates="student")

    def is_enrolled_on(self, day: dt.date) -> bool:
        """Matriculado no dia: matrícula até o dia e sem desistência até o dia."""
        if self.enrollment_date > day:
            return False
        return self.dropout_date is None or self.dropout_date > day


class Lesson(Base):
    """Aula realizada por uma turma em uma data. Uma aula por turma/dia."""

    __tablename__ = "lessons"
    __table_args__ = (UniqueConstraint("class_id", "date", name="uq_lesson_class_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = _organization_fk("lessons")
    class_id: Mapped[int] = mapped_column(ForeignKey("class_groups.id"))
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    methodology: Mapped[str | None] = mapped_column(Text)
    occurrences: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    class_group: Mapped[ClassGroup] = relationship(back_populates="lessons")
    attendances: Mapped[list["Attendance"]] = relationship(
        back_populates="lesson", cascade="all, delete-orphan"
    )
    created_by: Mapped[User | None] = relationship(foreign_keys=[created_by_user_id])
    updated_by: Mapped[User | None] = relationship(foreign_keys=[updated_by_user_id])


class Attendance(Base):
    """Presença de um aluno em uma aula. Única por (aula, aluno)."""

    __tablename__ = "attendances"
    __table_args__ = (
        UniqueConstraint("lesson_id", "student_id", name="uq_attendance_lesson_student"),
        CheckConstraint(
            "status IN ('presente', 'ausente', 'atestado')", name="ck_attendance_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lesson_id: Mapped[int] = mapped_column(ForeignKey("lessons.id", ondelete="CASCADE"))
    student_id: Mapped[int] = mapped_column(ForeignKey("students.id"))
    status: Mapped[str] = mapped_column(String(20))

    lesson: Mapped[Lesson] = relationship(back_populates="attendances")
    student: Mapped[Student] = relationship(back_populates="attendances")


class MonthlyReport(Base):
    """Relatório mensal de uma oficina (rascunho editável -> final).

    Campos de texto são preenchidos/revisados por pessoas. Métricas são
    calculadas ao vivo enquanto rascunho e congeladas em `snapshot_json`
    quando o relatório é finalizado.
    """

    __tablename__ = "monthly_reports"
    __table_args__ = (
        UniqueConstraint("workshop_id", "year", "month", name="uq_report_workshop_month"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = _organization_fk("monthly_reports")
    workshop_id: Mapped[int] = mapped_column(ForeignKey("workshops.id"))
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="rascunho")

    activities_summary: Mapped[str] = mapped_column(Text, default="")
    methodology_rating: Mapped[str | None] = mapped_column(String(20))
    methodology_notes: Mapped[str] = mapped_column(Text, default="")
    infrastructure: Mapped[str | None] = mapped_column(String(20))
    materials: Mapped[str | None] = mapped_column(String(20))
    logistics_comments: Mapped[str] = mapped_column(Text, default="")
    highlights_occurrences: Mapped[str] = mapped_column(Text, default="")
    next_month_planning: Mapped[str] = mapped_column(Text, default="")
    report_date: Mapped[dt.date | None] = mapped_column(Date)
    signature_name: Mapped[str] = mapped_column(String(160), default="")

    snapshot_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )
    finalized_at: Mapped[dt.datetime | None] = mapped_column(DateTime)

    workshop: Mapped[Workshop] = relationship()
