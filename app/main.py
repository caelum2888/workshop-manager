"""Aplicação FastAPI: API JSON em /api, telas HTML em /, docs em /docs."""

import secrets
import sys
import traceback
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.ai.report_summary import is_ai_configured
from app.config import (
    BASE_DIR,
    IS_PRODUCTION,
    LLM_MODEL,
    SESSION_COOKIE_NAME,
    SESSION_COOKIE_SECURE,
    require_secret_key,
)
from app.database import get_db
from app.dependencies import get_current_user
from app.models import User
from app.readiness import DatabaseNotReady, ensure_database_ready, schema_status
from app.routers import auth, catalog, imports, lessons, metrics, pages, reports, students
from app.routers.pages import templates as page_templates
from app.session import SignedCookieSessionMiddleware
from app.services.errors import ServiceError, Unauthenticated

# Páginas HTML (tudo fora de /api) recebem uma tela amigável nestes status;
# a API sempre continua JSON, e o redirect de sessão (303) nunca é tocado aqui.
_HTML_ERROR_COPY = {
    403: ("Acesso não permitido", "Você não tem permissão para acessar esta página."),
    404: ("Página não encontrada", "Página ou recurso não encontrado."),
    500: ("Algo deu errado", "Não foi possível concluir esta operação."),
}


def _is_api_path(request: Request) -> bool:
    return request.url.path.startswith("/api/")


def _render_error_page(request: Request, status_code: int, *, incident_code: str | None = None):
    title, message = _HTML_ERROR_COPY.get(status_code, _HTML_ERROR_COPY[500])
    return page_templates.TemplateResponse(
        request,
        "error.html",
        {"status_code": status_code, "title": title, "message": message, "incident_code": incident_code},
        status_code=status_code,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Schema é responsabilidade do Alembic (iniciar.bat / "alembic upgrade head"),
    # não do servidor: nunca cria um banco vazio em silêncio. Se não estiver
    # pronto, falha alto e cedo com orientação, em vez de subir "ok" quebrado.
    try:
        ensure_database_ready()
    except DatabaseNotReady as exc:
        print(f"\nERRO: {exc}\n")
        raise
    yield


app = FastAPI(
    title="Oficinas Manager",
    version="0.1.0",
    description=(
        "Gestão de oficinas: registro de aulas e presença, métricas mensais e "
        "relatório mensal. Erros retornam {detail, details}."
    ),
    lifespan=lifespan,
    docs_url=None if IS_PRODUCTION else "/docs",
    redoc_url=None if IS_PRODUCTION else "/redoc",
    # operationId = nome da função (ex.: get_monthly_metrics), útil para ferramentas de agentes.
    generate_unique_id_function=lambda route: route.name,
)

app.add_middleware(
    SignedCookieSessionMiddleware,
    secret_key=require_secret_key(),
    session_cookie=SESSION_COOKIE_NAME,
    https_only=SESSION_COOKIE_SECURE,
    max_age=60 * 60 * 12,
)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError):
    if not _is_api_path(request) and exc.status_code in (403, 404):
        return _render_error_page(request, exc.status_code)
    headers = {"WWW-Authenticate": "Session"} if isinstance(exc, Unauthenticated) else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.message, "details": exc.details},
        headers=headers,
    )


@app.exception_handler(IntegrityError)
async def integrity_error_handler(_request: Request, _exc: IntegrityError):
    return JSONResponse(
        status_code=409,
        content={"detail": "Operação viola uma regra de integridade (registro duplicado ou referência inválida).", "details": {}},
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    # Cobre rota inexistente (404 genérico do próprio Starlette). O redirect de
    # sessão (303, get_page_user) e qualquer outro status passam intactos.
    if not _is_api_path(request) and exc.status_code in (403, 404):
        return _render_error_page(request, exc.status_code)
    return await default_http_exception_handler(request, exc)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    code = secrets.token_hex(3).upper()
    # Detalhe técnico só no log do servidor; nunca na resposta.
    print(f"[incidente {code}] {request.method} {request.url.path}: {exc!r}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)
    message = f"Não foi possível concluir esta operação. Código: {code}"
    if _is_api_path(request):
        return JSONResponse(status_code=500, content={"detail": message, "details": {}})
    return _render_error_page(request, 500, incident_code=code)


@app.get("/health", tags=["sistema"], summary="Healthcheck")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
        ok, _current, _head = schema_status(db.get_bind())
    except Exception:
        ok = False
    if not ok:
        # Resposta mínima de propósito: nada de path do banco, SQL ou versão de revisão.
        return JSONResponse(status_code=503, content={"status": "unavailable", "reason": "database_schema_outdated"})
    return {"status": "ok"}


@app.get("/api/status", tags=["sistema"], summary="Status do sistema")
def get_status(_user: User = Depends(get_current_user)):
    return {"status": "ok", "ai_configured": is_ai_configured(), "ai_model": LLM_MODEL if is_ai_configured() else None}


app.mount("/static", StaticFiles(directory=BASE_DIR / "app" / "static"), name="static")

for module in (auth, catalog, students, lessons, metrics, reports, imports, pages):
    app.include_router(module.router)
