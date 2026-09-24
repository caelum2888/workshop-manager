"""Configuração lida de variáveis de ambiente (arquivo .env opcional)."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

load_dotenv(BASE_DIR / ".env")

DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{(DATA_DIR / 'oficinas.db').as_posix()}"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# A validação fica numa função, não numa checagem ao importar o módulo: scripts
# que só querem DATABASE_URL/BASE_DIR (backup, create_admin, seed) não usam
# cookies de sessão e não devem falhar por causa disso. Quem serve a aplicação
# web (app/main.py) é quem chama require_secret_key() antes de montar o
# middleware de sessão.
_running_tests = "pytest" in sys.modules
_raw_secret_key = os.getenv("SECRET_KEY", "").strip()
MIN_SECRET_KEY_LENGTH = 32


def require_secret_key() -> str:
    """Chave de sessão validada. Nunca aceita um valor padrão/conhecido em silêncio.

    Único bypass: a própria suíte de testes (sessões de teste são efêmeras e
    nunca saem da máquina), detectada por "pytest" já estar importado.
    """
    if _running_tests and not _raw_secret_key:
        return "test-only-insecure-key-" + "x" * 16
    if not _raw_secret_key:
        raise RuntimeError(
            "SECRET_KEY não configurada. Rode iniciar.bat (gera uma chave segura automaticamente) "
            "ou defina SECRET_KEY em um arquivo .env com pelo menos 32 caracteres aleatórios."
        )
    if len(_raw_secret_key) < MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(f"SECRET_KEY deve ter pelo menos {MIN_SECRET_KEY_LENGTH} caracteres.")
    return _raw_secret_key


SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "oficinas_session").strip()
SESSION_COOKIE_SECURE = IS_PRODUCTION or os.getenv(
    "SESSION_COOKIE_SECURE", "true" if IS_PRODUCTION else "false"
).strip().lower() in {"1", "true", "yes", "on"}

LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_MODEL = os.getenv("LLM_MODEL", "").strip() or "claude-opus-5"
