import sys

import pytest

from app import config
from app.ai import report_summary
from app.services import reports
from app.services.errors import Conflict, InvalidData
from app.services.export import build_docx
from tests.conftest import D, add_lesson, add_student


@pytest.fixture
def september(db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    bia = add_student(db, setup.klass, "Bia", D(2026, 9, 2))
    add_lesson(db, setup.klass, D(2026, 9, 3), {ana: "presente", bia: "ausente"},
               summary="Introdução à perspectiva.", occurrences="Faltou lápis 6B.")
    add_lesson(db, setup.klass, D(2026, 9, 8), {ana: "presente", bia: "presente"},
               summary="Desenho de observação.", methodology="Demonstração no quadro ajudou.")
    add_lesson(db, setup.klass, D(2026, 8, 27), {ana: "ausente"}, summary="Aula de agosto.")
    return setup


def complete(db, report_id, organization_id):
    return reports.update_report(
        db, report_id, {"methodology_rating": "bom", "infrastructure": "adequada", "materials": "faltando"},
        organization_id=organization_id,
    )


def test_generate_prefills_from_records_without_ai(db, september):
    r = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, use_ai=True, organization_id=september.org_id)

    assert r["created"] is True
    assert r["status"] == "rascunho"
    summary = r["fields"]["activities_summary"]
    assert "03/09" in summary and "Introdução à perspectiva." in summary
    assert "Aula de agosto." not in summary
    assert "Faltou lápis 6B." in r["fields"]["highlights_occurrences"]
    assert "Demonstração no quadro" in r["fields"]["methodology_notes"]
    assert r["fields"]["signature_name"] == "João Pereira"
    assert r["fields"]["methodology_rating"] is None  # campo humano não é inventado
    assert r["metrics"]["lessons_count"] == 2
    assert r["metrics"]["new_enrollments"] == 1
    assert r["participation"]["average"].startswith("75,0%")
    assert r["warnings"]  # avisa que a IA não está configurada


def test_generate_again_keeps_human_edits(db, september):
    first = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, organization_id=september.org_id)
    reports.update_report(db, first["id"], {"activities_summary": "Texto revisado"}, organization_id=september.org_id)
    again = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, organization_id=september.org_id)
    assert again["id"] == first["id"]
    assert again["created"] is False
    assert again["fields"]["activities_summary"] == "Texto revisado"


def test_finalize_requires_human_fields(db, september):
    r = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, organization_id=september.org_id)
    with pytest.raises(InvalidData) as exc:
        reports.finalize_report(db, r["id"], organization_id=september.org_id)
    missing = {m["field"] for m in exc.value.details["missing"]}
    assert missing == {"methodology_rating", "infrastructure", "materials"}


def test_finalized_report_freezes_metrics_and_blocks_edits(db, september):
    r = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, organization_id=september.org_id)
    complete(db, r["id"], september.org_id)
    final = reports.finalize_report(db, r["id"], organization_id=september.org_id)
    assert final["status"] == "final"
    assert final["metrics_source"] == "snapshot"

    add_lesson(db, september.klass, D(2026, 9, 10), summary="Aula lançada depois")
    frozen = reports.get_report(db, r["id"], organization_id=september.org_id)
    assert frozen["metrics"]["lessons_count"] == 2

    with pytest.raises(Conflict):
        reports.update_report(db, r["id"], {"materials": "suficientes"}, organization_id=september.org_id)

    reopened = reports.reopen_report(db, r["id"], organization_id=september.org_id)
    assert reopened["metrics_source"] == "live"
    assert reopened["metrics"]["lessons_count"] == 3


def test_ai_failure_falls_back_to_original_records(db, september, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "chave-de-teste")

    def boom(*_args, **_kwargs):
        raise RuntimeError("indisponível")

    monkeypatch.setattr(report_summary, "_call_llm", boom)
    r = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, organization_id=september.org_id)
    s = reports.suggest_report_text(db, r["id"], "activities_summary", use_ai=True, organization_id=september.org_id)
    assert s["source"] == "records"
    assert "Introdução à perspectiva." in s["text"]
    assert s["warning"]


def test_ai_failure_fallback_does_not_require_optional_sdk(db, september, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "chave-de-teste")
    monkeypatch.setattr(
        report_summary,
        "_call_llm",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("indisponível")),
    )
    monkeypatch.setitem(sys.modules, "anthropic", None)

    report = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, organization_id=september.org_id)
    suggestion = reports.suggest_report_text(
        db, report["id"], "activities_summary", use_ai=True,
        organization_id=september.org_id,
    )

    assert suggestion["source"] == "records"
    assert suggestion["warning"].startswith("Não foi possível usar a IA.")


def test_ai_text_is_only_a_suggestion(db, september, monkeypatch):
    monkeypatch.setattr(config, "LLM_API_KEY", "chave-de-teste")
    monkeypatch.setattr(report_summary, "_call_llm", lambda *a, **k: "Texto consolidado pela IA.")
    r = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, organization_id=september.org_id)
    before = reports.get_report(db, r["id"], organization_id=september.org_id)["fields"]["highlights_occurrences"]
    s = reports.suggest_report_text(db, r["id"], "highlights_occurrences", use_ai=True, organization_id=september.org_id)
    assert s == {"field": "highlights_occurrences", "text": "Texto consolidado pela IA.", "source": "ai", "warning": s["warning"]}
    assert reports.get_report(db, r["id"], organization_id=september.org_id)["fields"]["highlights_occurrences"] == before


def test_docx_export(db, september):
    r = reports.generate_monthly_report(db, september.workshop.id, 2026, 9, organization_id=september.org_id)
    content = build_docx(reports.get_report(db, r["id"], organization_id=september.org_id))
    assert content[:2] == b"PK"
