"""v0.2.1 etapa 2: páginas HTML de erro, 500 sanitizado, API continua JSON."""

import re

from app.routers import metrics as metrics_router
from app.routers import pages as pages_router


def _boom(*_a, **_k):
    raise RuntimeError("detalhe sensível: SELECT senha FROM users WHERE path='C:\\segredo'")


def _lenient(client):
    """TestClient.raise_server_exceptions=True (padrão) propaga a exceção ao teste em vez de
    passar pelo handler, só durante testes; em produção real o handler sempre roda. Aqui
    desligamos isso especificamente para testar o handler de 500."""
    client._transport.raise_server_exceptions = False
    return client


# --------------------------------------------------------------- páginas HTML
def test_html_403_is_friendly_not_json(client, db, setup):
    professor = client.post(
        "/api/users",
        json={
            "name": "Professor", "email": "prof@example.test", "password": "senha-professor-123",
            "role": "professor", "workshop_ids": [setup.workshop.id],
        },
    )
    assert professor.status_code == 201
    client.post("/logout")
    client.post("/login", data={"email": "prof@example.test", "password": "senha-professor-123"})

    response = client.get("/turmas")
    assert response.status_code == 403
    assert "text/html" in response.headers["content-type"]
    assert "Você não tem permissão" in response.text
    assert "detail" not in response.text  # nada de JSON cru
    assert "{" not in response.text.split("<body")[1].split("</body>")[0][:50]


def test_html_404_for_missing_report(client, setup):
    response = client.get("/relatorios/999999")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Página ou recurso não encontrado." in response.text


def test_html_404_for_unmatched_route(client):
    response = client.get("/esta-pagina-nao-existe")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "Página ou recurso não encontrado." in response.text


def test_error_page_has_no_technical_details(client, setup):
    response = client.get("/relatorios/999999")
    lowered = response.text.lower()
    for leak in ("traceback", "sqlalchemy", "select ", "site-packages", "\\app\\", "/app/"):
        assert leak not in lowered


# ---------------------------------------------------------------- API em JSON
def test_api_404_stays_json(client, setup):
    response = client.get("/api/reports/999999")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Recurso não encontrado.", "details": {}}


def test_api_unmatched_route_stays_json(client):
    response = client.get("/api/isto-nao-existe")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Not Found"}


def test_api_401_unchanged(anonymous_client):
    response = anonymous_client.get("/api/students")
    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"detail": "Faça login para continuar.", "details": {}}
    assert response.headers["www-authenticate"] == "Session"


# --------------------------------------------------------- 500 sem vazamento
def test_html_500_has_incident_code_and_no_leak(client, monkeypatch):
    monkeypatch.setattr(pages_router.metrics, "get_monthly_metrics", _boom)
    response = _lenient(client).get("/")
    assert response.status_code == 500
    assert "text/html" in response.headers["content-type"]
    assert "Não foi possível concluir esta operação." in response.text
    assert "segredo" not in response.text and "SELECT" not in response.text and "RuntimeError" not in response.text
    match = re.search(r"Código:\s*([0-9A-F]{6})", response.text)
    assert match, response.text


def test_api_500_has_incident_code_and_no_leak(client, setup, monkeypatch):
    monkeypatch.setattr(metrics_router.metrics, "get_monthly_metrics", _boom)
    response = _lenient(client).get("/api/metrics/monthly?year=2026&month=9")
    assert response.status_code == 500
    body = response.json()
    assert body["detail"].startswith("Não foi possível concluir esta operação. Código: ")
    assert "segredo" not in body["detail"] and "RuntimeError" not in body["detail"]


def test_500_incident_code_is_logged(client, monkeypatch, capsys):
    monkeypatch.setattr(pages_router.metrics, "get_monthly_metrics", _boom)
    response = _lenient(client).get("/")
    code = re.search(r"Código:\s*([0-9A-F]{6})", response.text).group(1)
    captured = capsys.readouterr()
    assert code in captured.err  # mesmo código no log do servidor
    assert "RuntimeError" in captured.err  # detalhe técnico fica só no log


def test_generic_handler_does_not_shadow_specific_ones(client, setup):
    """O handler genérico de Exception não deve capturar o que já tem tratamento específico."""
    assert client.get("/api/reports/999999").status_code == 404  # ServiceError, não 500
    assert client.get("/api/isto-nao-existe").status_code == 404  # HTTPException, não 500
