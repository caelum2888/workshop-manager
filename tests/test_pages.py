from tests.conftest import D, add_lesson, add_student


def test_pages_render(client, db, setup):
    ana = add_student(db, setup.klass, "Ana", D(2026, 7, 1))
    add_lesson(db, setup.klass, D(2026, 9, 1), {ana: "presente"})
    report = client.post("/api/reports/generate", json={"workshop_id": setup.workshop.id, "year": 2026, "month": 9}).json()

    for url in ["/", "/aulas/registrar", "/aulas", "/dashboard", "/alunos", "/turmas", "/relatorios",
                "/usuarios", "/senha", f"/relatorios/{report['id']}", f"/relatorios/{report['id']}/imprimir"]:
        response = client.get(url)
        assert response.status_code == 200, url

    printed = client.get(f"/relatorios/{report['id']}/imprimir").text
    assert "Relatório Mensal de Atividades: Oficinas Itinerantes" in printed
    assert "☒ Setembro" in printed
    assert "RASCUNHO" in printed


def test_docs_and_status(client):
    assert client.get("/docs").status_code == 200
    assert client.get("/api/status").json()["ai_configured"] is False


def test_destructive_actions_have_explicit_confirmations(client):
    assert "todas as presenças dela" in client.get("/aulas").text
    assert "Registrar a desistência" in client.get("/alunos").text
    assert "O histórico será preservado" in client.get("/turmas").text


def test_month_without_lessons_and_invalid_period_return_clean_responses(client, setup):
    empty = client.get("/api/metrics/monthly?year=2026&month=11")
    assert empty.status_code == 200
    assert empty.json()["lessons_count"] == 0
    assert empty.json()["attendance_rate_percent"] is None

    invalid = client.get("/api/metrics/monthly?year=2026&month=13")
    assert invalid.status_code == 422
    assert "Mês inválido" in invalid.json()["detail"]
