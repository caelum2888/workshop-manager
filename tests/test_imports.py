"""Importação de turmas e alunos por planilha: parser, validação, dry-run,
importação, autorização e isolamento multi-tenant."""

import io

import openpyxl
import pytest

from app.models import ClassGroup, Student, Workshop
from app.services import auth, imports
from app.services.organizations import get_or_create_default_organization

CSV_HEADER = "oficina,turma,horario,oficineiro_email,aluno_nome,data_matricula,aluno_status"


def csv_bytes(rows: list[str], header: str = CSV_HEADER) -> bytes:
    return ("\n".join([header, *rows]) + "\n").encode("utf-8")


def xlsx_bytes(rows: list[list], header=None) -> bytes:
    header = header or imports.TEMPLATE_COLUMNS
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(row)
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def make_professor(db, org_id, email="larissa@example.com", name="Larissa"):
    return auth.create_user(db, {
        "name": name, "email": email, "password": "senha-professor-123",
        "role": "professor", "organization_id": org_id,
    })


def upload(client, path, content: bytes, filename: str, content_type="text/csv"):
    return client.post(path, files={"file": (filename, content, content_type)})


# ===================================================================== parser
class TestParser:
    def test_valid_csv_is_parsed(self, db, setup):
        result = imports.run_import(
            db, "turmas.csv",
            csv_bytes(["Ballet,Ballet 14h,14:00,,Ana Silva,2026-09-01,ativo"]),
            setup.org_id, dry_run=True,
        )
        assert result.total_rows == 1
        assert result.rows[0].status == "criar"

    def test_valid_xlsx_is_parsed(self, db, setup):
        content = xlsx_bytes([["Ballet", "Ballet 14h", "14:00", "", "Ana Silva", "2026-09-01", "ativo"]])
        result = imports.run_import(db, "turmas.xlsx", content, setup.org_id, dry_run=True)
        assert result.total_rows == 1
        assert result.rows[0].status == "criar"

    def test_xlsx_real_date_cell_is_recognized(self, db, setup):
        """openpyxl entrega célula de data como datetime.datetime, não string; isso não
        pode virar "2026-09-01 00:00:00" (texto) antes de chegar em _parse_date."""
        import datetime as dt

        content = xlsx_bytes([["Ballet", "Ballet 14h", "14:00", "", "Ana Silva", dt.date(2026, 9, 1), "ativo"]])
        result = imports.run_import(db, "turmas.xlsx", content, setup.org_id, dry_run=True)
        assert result.rows[0].status == "criar", result.rows[0].message

    def test_fields_are_normalized_trim_and_lowercase_email(self, db, setup):
        prof = make_professor(db, setup.org_id, email="prof@example.com")
        result = imports.run_import(
            db, "turmas.csv",
            csv_bytes(["  Ballet  , Ballet 14h ,14:00, PROF@EXAMPLE.COM ,  Ana Silva  ,2026-09-01,ativo"]),
            setup.org_id, dry_run=True,
        )
        row = result.rows[0]
        assert row.oficina == "Ballet" and row.turma == "Ballet 14h" and row.aluno == "Ana Silva"
        assert row.status == "criar", row.message

    def test_header_with_extra_spaces_and_case_is_tolerated(self, db, setup):
        header = "Oficina, Turma , HORARIO,oficineiro_email,ALUNO_NOME,data_matricula"
        result = imports.run_import(
            db, "turmas.csv", csv_bytes(["Ballet,Ballet 14h,,, Ana,2026-09-01"], header=header),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "criar", result.rows[0].message

    def test_invalid_extension_is_rejected(self, db, setup):
        with pytest.raises(imports.InvalidData, match=r"\.csv ou \.xlsx"):
            imports.run_import(db, "turmas.pdf", b"lixo", setup.org_id, dry_run=True)

    @pytest.mark.parametrize("alias", ["aluno", "nome", "nome do aluno"])
    def test_student_name_header_aliases_are_accepted(self, db, setup, alias):
        header = f"oficina,turma,horario,oficineiro_email,{alias},data_matricula,aluno_status"
        result = imports.run_import(
            db, "t.csv", csv_bytes(["Ballet,Ballet 14h,,,Ana,2026-09-01,ativo"], header=header),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "criar", result.rows[0].message
        assert result.rows[0].aluno == "Ana"

    @pytest.mark.parametrize(
        "alias", ["professor", "professora", "email professor", "e-mail do professor", "oficineiro_email"]
    )
    def test_instructor_email_header_aliases_are_accepted(self, db, setup, alias):
        """"oficineiro_email" é o nome oficial anterior da coluna; continua aceito como
        alias legado (não quebra planilhas antigas), mas o oficial agora é professor_email."""
        make_professor(db, setup.org_id, email="prof@example.com")
        header = f"oficina,turma,horario,{alias},aluno_nome,data_matricula,aluno_status"
        result = imports.run_import(
            db, "t.csv", csv_bytes(["Ballet,Ballet 14h,,prof@example.com,Ana,2026-09-01,ativo"], header=header),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "criar", result.rows[0].message

    def test_official_header_is_professor_email(self, db, setup):
        make_professor(db, setup.org_id, email="prof@example.com")
        header = "oficina,turma,horario,professor_email,aluno_nome,data_matricula,aluno_status"
        result = imports.run_import(
            db, "t.csv", csv_bytes(["Ballet,Ballet 14h,,prof@example.com,Ana,2026-09-01,ativo"], header=header),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "criar", result.rows[0].message

    def test_template_uses_professor_email_as_official_column(self):
        assert "professor_email" in imports.TEMPLATE_COLUMNS
        assert "oficineiro_email" not in imports.TEMPLATE_COLUMNS

    def test_duplicate_column_after_alias_resolution_is_rejected(self, db, setup):
        header = "oficina,turma,horario,oficineiro_email,aluno_nome,aluno,data_matricula,aluno_status"
        with pytest.raises(imports.InvalidData, match="duplicada"):
            imports.run_import(
                db, "t.csv", csv_bytes(["Ballet,Ballet 14h,,,Ana,Ana,2026-09-01,ativo"], header=header),
                setup.org_id, dry_run=True,
            )


# =================================================================== validação
class TestValidation:
    def test_professor_not_found_is_a_row_error(self, db, setup):
        result = imports.run_import(
            db, "t.csv",
            csv_bytes(["Ballet,Ballet 14h,,naoexiste@example.com,Ana,2026-09-01"]),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "erro"
        assert "professor não encontrado" in result.rows[0].message.lower()
        assert result.errors == 1

    def test_professor_from_another_organization_is_treated_as_not_found(self, db, setup):
        from app.models import Organization

        other = Organization(name="Outra", slug="outra-imports")
        db.add(other)
        db.commit()
        make_professor(db, other.id, email="externo@example.com")

        result = imports.run_import(
            db, "t.csv",
            csv_bytes(["Ballet,Ballet 14h,,externo@example.com,Ana,2026-09-01"]),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "erro"
        assert "professor não encontrado" in result.rows[0].message.lower()
        assert "outra" not in result.rows[0].message.lower()  # nunca revela o outro tenant

    def test_invalid_date_is_a_row_error(self, db, setup):
        result = imports.run_import(
            db, "t.csv", csv_bytes(["Ballet,Ballet 14h,,,Ana,31/31/2026"]), setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "erro"
        assert "data" in result.rows[0].message.lower()

    def test_accepted_date_formats(self, db, setup):
        result = imports.run_import(
            db, "t.csv",
            csv_bytes(["Ballet,Ballet 14h,,,Ana,2026-09-01", "Ballet,Ballet 14h,,,Bia,01/09/2026"]),
            setup.org_id, dry_run=True,
        )
        assert [r.status for r in result.rows] == ["criar", "criar"], [r.message for r in result.rows]

    def test_duplicate_row_in_same_file_is_an_error(self, db, setup):
        result = imports.run_import(
            db, "t.csv",
            csv_bytes(["Ballet,Ballet 14h,,,Ana,2026-09-01", "Ballet,Ballet 14h,,,Ana,2026-09-05"]),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "criar"
        assert result.rows[1].status == "erro"
        assert "linha 2" in result.rows[1].message.lower()

    def test_student_already_in_target_class_is_existing_not_error(self, db, setup):
        from tests.conftest import D, add_student

        add_student(db, setup.klass, "Ana Silva", D(2026, 7, 1))
        result = imports.run_import(
            db, "t.csv",
            csv_bytes([f"{setup.workshop.name},{setup.klass.name},,,Ana Silva,2026-09-01"]),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "existente"
        assert result.errors == 0 and result.ok_to_import

    def test_student_with_same_name_in_another_class_is_a_conflict(self, db, setup):
        from tests.conftest import D, add_student

        add_student(db, setup.other, "Ana Silva", D(2026, 7, 1))  # matriculada em outra turma
        result = imports.run_import(
            db, "t.csv",
            csv_bytes([f"{setup.workshop.name},{setup.klass.name},,,Ana Silva,2026-09-01"]),
            setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "conflito"
        assert setup.other.name in result.rows[0].message
        assert not result.ok_to_import  # conflito bloqueia a confirmação

    def test_new_workshop_and_class_are_detected(self, db, setup):
        result = imports.run_import(
            db, "t.csv", csv_bytes(["Nova Oficina,Nova Turma,,,Ana,2026-09-01"]), setup.org_id, dry_run=True,
        )
        assert result.workshops_to_create == 1
        assert result.classes_to_create == 1
        assert result.rows[0].status == "criar"

    def test_existing_workshop_is_reused_not_recreated(self, db, setup):
        result = imports.run_import(
            db, "t.csv",
            csv_bytes([f"{setup.workshop.name},Nova Turma,,,Ana,2026-09-01"]),
            setup.org_id, dry_run=True,
        )
        assert result.workshops_to_create == 0  # oficina já existe
        assert result.classes_to_create == 1  # turma é nova


# ------------------------------------------------------------- arquivos malformados
class TestMalformedFiles:
    def test_empty_file(self, db, setup):
        with pytest.raises(imports.InvalidData, match="vazia"):
            imports.run_import(db, "t.csv", b"", setup.org_id, dry_run=True)

    def test_missing_header_column(self, db, setup):
        with pytest.raises(imports.InvalidData, match="Cabeçalho incompleto"):
            imports.run_import(db, "t.csv", b"oficina,turma\nBallet,Ballet 14h\n", setup.org_id, dry_run=True)

    def test_file_with_header_only_no_data_rows(self, db, setup):
        with pytest.raises(imports.InvalidData, match="nenhuma linha"):
            imports.run_import(db, "t.csv", (CSV_HEADER + "\n").encode(), setup.org_id, dry_run=True)

    def test_row_missing_required_column_value(self, db, setup):
        result = imports.run_import(
            db, "t.csv", csv_bytes(["Ballet,,,,Ana,2026-09-01"]), setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "erro"
        assert "turma" in result.rows[0].message.lower()

    def test_schedule_over_column_limit_is_a_row_error(self, db, setup):
        """horário é texto livre no domínio (ClassGroup.schedule); o limite real é o da coluna (120)."""
        result = imports.run_import(
            db, "t.csv", csv_bytes([f"Ballet,Ballet 14h,{'x' * 130},,Ana,2026-09-01"]), setup.org_id, dry_run=True,
        )
        assert result.rows[0].status == "erro"
        assert "muito longo" in result.rows[0].message.lower()

    def test_file_too_large_is_rejected(self, db, setup):
        big = csv_bytes(["Ballet,Ballet 14h,,,Ana,2026-09-01"]) + b"#" * (imports.MAX_FILE_BYTES + 1)
        with pytest.raises(imports.InvalidData, match="grande"):
            imports.run_import(db, "t.csv", big, setup.org_id, dry_run=True)

    def test_too_many_rows_is_rejected(self, db, setup):
        rows = [f"Ballet,Ballet 14h,,,Aluno {i},2026-09-01" for i in range(imports.MAX_ROWS + 1)]
        with pytest.raises(imports.InvalidData, match="Muitas linhas"):
            imports.run_import(db, "t.csv", csv_bytes(rows), setup.org_id, dry_run=True)

    def test_corrupted_xlsx_gives_a_human_message_not_a_traceback(self, db, setup):
        with pytest.raises(imports.InvalidData, match="corrompido"):
            imports.run_import(db, "t.xlsx", b"isto nao e um xlsx valido", setup.org_id, dry_run=True)


# ======================================================================= dry-run
class TestDryRun:
    def test_preview_never_writes_to_the_database(self, db, setup):
        before = (
            db.query(Workshop).count(), db.query(ClassGroup).count(), db.query(Student).count(),
        )
        imports.run_import(
            db, "t.csv",
            csv_bytes(["Nova Oficina,Nova Turma,,,Ana,2026-09-01", "Nova Oficina,Nova Turma,,,Bia,2026-09-01"]),
            setup.org_id, dry_run=True,
        )
        after = (db.query(Workshop).count(), db.query(ClassGroup).count(), db.query(Student).count())
        assert before == after

    def test_dry_run_counts_are_correct(self, db, setup):
        from tests.conftest import D, add_student

        add_student(db, setup.klass, "Aluno Existente", D(2026, 7, 1))
        result = imports.run_import(
            db, "t.csv",
            csv_bytes([
                f"{setup.workshop.name},{setup.klass.name},,,Aluno Existente,2026-09-01",  # existente
                f"{setup.workshop.name},{setup.klass.name},,,Novo Aluno,2026-09-01",  # criar
                "Oficina Nova,Turma Nova,,,Outro Aluno,2026-09-01",  # oficina+turma novas, criar
            ]),
            setup.org_id, dry_run=True,
        )
        assert result.total_rows == 3
        assert result.students_existing == 1
        assert result.students_to_create == 2
        assert result.workshops_to_create == 1
        assert result.classes_to_create == 1
        assert result.errors == 0
        assert result.ok_to_import


# ===================================================================== importação
class TestImport:
    def test_creates_workshop_class_and_students_with_correct_organization(self, db, setup):
        result = imports.run_import(
            db, "t.csv",
            csv_bytes(["Nova Oficina,Nova Turma,14h,,Ana Silva,2026-09-01", "Nova Oficina,Nova Turma,14h,,Bia Souza,2026-09-01"]),
            setup.org_id, dry_run=False,
        )
        assert result.imported is True
        workshop = db.scalar(db.query(Workshop).filter_by(name="Nova Oficina").statement)
        assert workshop.organization_id == setup.org_id
        classes = db.query(ClassGroup).filter_by(workshop_id=workshop.id).all()
        assert len(classes) == 1 and classes[0].name == "Nova Turma" and classes[0].organization_id == setup.org_id
        students = db.query(Student).filter_by(class_id=classes[0].id).all()
        assert {s.name for s in students} == {"Ana Silva", "Bia Souza"}
        assert all(s.organization_id == setup.org_id for s in students)

    def test_associates_correct_instructor(self, db, setup):
        prof = make_professor(db, setup.org_id, email="prof@example.com")
        result = imports.run_import(
            db, "t.csv", csv_bytes(["Nova Oficina,Nova Turma,,prof@example.com,Ana,2026-09-01"]),
            setup.org_id, dry_run=False,
        )
        assert result.imported
        workshop = db.scalar(db.query(Workshop).filter_by(name="Nova Oficina").statement)
        assert workshop.instructor_id == prof["instructor_id"]

    def test_rollback_on_unexpected_error_creates_nothing(self, db, setup, monkeypatch):
        """Oficina e turma já passaram por flush() (estão na transação, mas não commitadas) quando
        a criação do aluno falha; se o lote não for atômico, elas sobreviveriam ao rollback."""
        before = (db.query(Workshop).count(), db.query(ClassGroup).count(), db.query(Student).count())

        # Só falha ao adicionar o Student (fase de gravação); a query de conflito de nome em
        # _process_row também usa `Student`, então um patch na classe quebraria a validação
        # (antes de qualquer gravação) em vez da criação do aluno de verdade.
        real_add = db.add

        def boom_on_student(obj, *a, **k):
            if isinstance(obj, Student):
                raise RuntimeError("falha inesperada ao criar aluno")
            return real_add(obj, *a, **k)

        monkeypatch.setattr(db, "add", boom_on_student)
        with pytest.raises(RuntimeError):
            imports.run_import(
                db, "t.csv", csv_bytes(["Nova Oficina,Nova Turma,,,Ana,2026-09-01"]), setup.org_id, dry_run=False,
            )
        db.rollback()
        after = (db.query(Workshop).count(), db.query(ClassGroup).count(), db.query(Student).count())
        assert before == after, "oficina/turma já com flush() não foram desfeitas pelo rollback"

    def test_confirm_refuses_when_there_are_errors(self, db, setup):
        result = imports.run_import(
            db, "t.csv", csv_bytes(["Ballet,Ballet 14h,,naoexiste@example.com,Ana,2026-09-01"]),
            setup.org_id, dry_run=False,
        )
        assert result.imported is False
        assert db.query(Workshop).filter_by(name="Ballet").count() == 0

    def test_running_the_same_file_twice_does_not_duplicate(self, db, setup):
        content = csv_bytes([
            "Ballet,Ballet 14h,14h,,Ana Silva,2026-09-01",
            "Ballet,Ballet 14h,14h,,Beatriz Souza,2026-09-01",
            "Ballet,Ballet 15h,15h,,Carla Lima,2026-09-01",
        ])
        first = imports.run_import(db, "t.csv", content, setup.org_id, dry_run=False)
        assert first.imported and first.students_to_create == 3

        second = imports.run_import(db, "t.csv", content, setup.org_id, dry_run=False)
        assert second.students_to_create == 0
        assert second.students_existing == 3
        assert second.workshops_to_create == 0
        assert second.classes_to_create == 0

        assert db.query(Workshop).filter_by(name="Ballet").count() == 1
        assert db.query(Student).filter(Student.name.in_(["Ana Silva", "Beatriz Souza", "Carla Lima"])).count() == 3


# ================================================================ HTTP / autorização
class TestHttpAndAuthorization:
    def test_professor_gets_403_on_every_import_route(self, client, db, setup):
        client.post("/api/users", json={
            "name": "Professor", "email": "prof-import@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [setup.workshop.id],
        })
        client.post("/logout")
        client.post("/login", data={"email": "prof-import@example.test", "password": "senha-professor-123"})

        assert client.get("/importar").status_code == 403
        assert client.get("/api/imports/students/template.csv").status_code == 403
        content = csv_bytes(["Ballet,Ballet 14h,,,Ana,2026-09-01"])
        assert upload(client, "/api/imports/students/preview", content, "t.csv").status_code == 403
        assert upload(client, "/api/imports/students/confirm", content, "t.csv").status_code == 403

    def test_admin_page_and_templates_are_reachable(self, client):
        assert client.get("/importar").status_code == 200
        csv_resp = client.get("/api/imports/students/template.csv")
        assert csv_resp.status_code == 200 and csv_resp.headers["content-type"].startswith("text/csv")
        xlsx_resp = client.get("/api/imports/students/template.xlsx")
        assert xlsx_resp.status_code == 200 and "spreadsheet" in xlsx_resp.headers["content-type"]

    def test_full_preview_then_confirm_flow_over_http(self, client, setup):
        content = csv_bytes(["Nova Oficina,Nova Turma,,,Ana Silva,2026-09-01"])
        preview = upload(client, "/api/imports/students/preview", content, "t.csv")
        assert preview.status_code == 200
        body = preview.json()
        assert body["ok_to_import"] is True
        assert body["summary"]["students_to_create"] == 1

        confirm = upload(client, "/api/imports/students/confirm", content, "t.csv")
        assert confirm.status_code == 200
        assert confirm.json()["imported"] is True
        assert client.get("/api/workshops").json()  # criado de verdade, visível pela API normal

    def test_admin_of_org_a_cannot_reference_instructor_from_org_b(self, client, db, setup, org):
        from app.models import Organization

        other = Organization(name="Outra", slug="outra-imports-http")
        db.add(other)
        db.commit()
        make_professor(db, other.id, email="deoutratenant@example.com")

        content = csv_bytes(["Ballet,Ballet 14h,,deoutratenant@example.com,Ana,2026-09-01"])
        preview = upload(client, "/api/imports/students/preview", content, "t.csv").json()
        assert preview["rows"][0]["status"] == "erro"
        assert "outra" not in preview["rows"][0]["message"].lower()

        confirm = upload(client, "/api/imports/students/confirm", content, "t.csv")
        assert confirm.status_code == 422
        assert db.query(Workshop).filter_by(name="Ballet").count() == 0

    def test_import_never_leaks_organization_id_in_the_response(self, client, setup):
        content = csv_bytes(["Nova Oficina,Nova Turma,,,Ana,2026-09-01"])
        body = upload(client, "/api/imports/students/preview", content, "t.csv").json()
        assert "organization_id" not in str(body)
