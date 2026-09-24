"""Schemas Pydantic de entrada da API.

As saídas são dicionários JSON montados nos services (formato estável e
documentado nas docstrings), o que facilita o uso por agentes/LLMs.
"""

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field

StudentStatus = Literal["ativo", "desistente"]
AttendanceStatus = Literal["presente", "ausente", "atestado"]
MethodologyRating = Literal["excelente", "bom", "regular", "atencao"]
Infrastructure = Literal["adequada", "inadequada"]
Materials = Literal["suficientes", "faltando"]
SuggestionField = Literal["activities_summary", "highlights_occurrences", "methodology_notes"]
UserRole = Literal["admin", "professor"]


# ---- Usuários ----
class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=8, max_length=200)
    role: UserRole = "professor"
    active: bool = True
    workshop_ids: list[int] = Field(default_factory=list)
    # Vincula a um professor legado sem conta (Instructor sem User); só role=professor.
    link_instructor_id: int | None = None


class UserUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=3, max_length=200)
    password: str | None = Field(default=None, min_length=8, max_length=200)
    active: bool | None = None
    workshop_ids: list[int] | None = None


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=200)
    confirm_password: str


# ---- Oficineiros ----
class InstructorIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str | None = None
    active: bool = True


class InstructorUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    email: str | None = None
    active: bool | None = None


# ---- Oficinas ----
class WorkshopIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    language: str = ""
    location: str = ""
    instructor_id: int | None = None
    active: bool = True


class WorkshopUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    language: str | None = None
    location: str | None = None
    instructor_id: int | None = None
    active: bool | None = None


# ---- Turmas ----
class ClassIn(BaseModel):
    workshop_id: int
    name: str = Field(min_length=1, max_length=120)
    schedule: str | None = None
    location: str | None = None
    active: bool = True


class ClassUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    schedule: str | None = None
    location: str | None = None
    active: bool | None = None


# ---- Alunos ----
class StudentIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    class_id: int
    enrollment_date: dt.date = Field(default_factory=dt.date.today)
    status: StudentStatus = "ativo"
    dropout_date: dt.date | None = None


class StudentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    class_id: int | None = None
    enrollment_date: dt.date | None = None
    status: StudentStatus | None = None
    dropout_date: dt.date | None = None


class DropoutIn(BaseModel):
    dropout_date: dt.date = Field(default_factory=dt.date.today)


# ---- Aulas e presença ----
class AttendanceRecordIn(BaseModel):
    student_id: int
    status: AttendanceStatus


class LessonIn(BaseModel):
    class_id: int
    date: dt.date
    summary: str = ""
    methodology: str | None = None
    occurrences: str | None = None
    notes: str | None = None
    attendance: list[AttendanceRecordIn] = Field(default_factory=list)


class LessonUpdate(BaseModel):
    date: dt.date | None = None
    summary: str | None = None
    methodology: str | None = None
    occurrences: str | None = None
    notes: str | None = None
    attendance: list[AttendanceRecordIn] | None = None


class AttendanceIn(BaseModel):
    lesson_id: int
    records: list[AttendanceRecordIn]


# ---- Relatórios ----
class ReportGenerateIn(BaseModel):
    workshop_id: int
    year: int = Field(ge=2000, le=2100)
    month: int = Field(ge=1, le=12)
    use_ai: bool = False


class ReportUpdate(BaseModel):
    activities_summary: str | None = None
    methodology_rating: MethodologyRating | None = None
    methodology_notes: str | None = None
    infrastructure: Infrastructure | None = None
    materials: Materials | None = None
    logistics_comments: str | None = None
    highlights_occurrences: str | None = None
    next_month_planning: str | None = None
    report_date: dt.date | None = None
    signature_name: str | None = None


class SuggestionIn(BaseModel):
    field: SuggestionField
    use_ai: bool = True
