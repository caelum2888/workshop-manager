"""Prontidão do banco: o schema está no head do Alembic?

Não confundir com ``database.init_db`` (cria tabelas via ``create_all``, usado
por scripts/testes). Isto aqui só LÊ o estado do banco, nunca o altera.
"""

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.config import BASE_DIR


class DatabaseNotReady(RuntimeError):
    """O servidor não deve iniciar: o banco não existe, está incompleto ou desatualizado."""


def _alembic_head() -> str | None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(BASE_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BASE_DIR / "migrations"))
    return ScriptDirectory.from_config(cfg).get_current_head()


def schema_status(engine: Engine) -> tuple[bool, str | None, str | None]:
    """(pronto, revisão_atual, revisão_head).

    ``create_all`` sozinho (sem carimbo do Alembic) NÃO conta como pronto: é
    exatamente o cenário de "banco vazio criado silenciosamente" que este
    módulo existe para evitar.
    """
    head = _alembic_head()
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        return False, None, head
    with engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    return current == head, current, head


def ensure_database_ready(engine: Engine | None = None) -> None:
    """Levanta ``DatabaseNotReady`` com orientação clara se o banco não estiver pronto."""
    if engine is None:
        from app.database import engine as default_engine

        engine = default_engine
    try:
        ok, current, head = schema_status(engine)
    except Exception as exc:  # banco inacessível (arquivo/pasta ausente, conexão recusada, etc.)
        raise DatabaseNotReady(
            "Não foi possível conectar ao banco de dados. Verifique se a pasta/servidor "
            "existe e se DATABASE_URL está correto."
        ) from exc
    if ok:
        return
    if current is None:
        raise DatabaseNotReady(
            "Banco de dados não inicializado (schema ausente ou incompleto). "
            "Rode: python -m alembic upgrade head  (ou use iniciar.bat)."
        )
    raise DatabaseNotReady(
        f"Banco de dados desatualizado (versão {current}, esperado {head}). "
        "Rode: python -m alembic upgrade head  (faça backup antes: scripts\\backup.ps1)."
    )
