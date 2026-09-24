"""app.readiness: o servidor nunca deve achar 'pronto' um banco vazio ou desatualizado."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.pool import StaticPool

from app import config as app_config
from app.database import Base, make_engine
from app.readiness import DatabaseNotReady, ensure_database_ready, schema_status

ROOT = Path(__file__).resolve().parent.parent


def _alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    return cfg


def _upgrade(monkeypatch, url: str, revision: str = "head") -> None:
    """migrations/env.py lê app.config.DATABASE_URL diretamente, ignorando o
    Config passado; por isso o monkeypatch, e não cfg.set_main_option()."""
    monkeypatch.setattr(app_config, "DATABASE_URL", url)
    command.upgrade(_alembic_config(), revision)


def test_schema_status_not_ready_for_completely_empty_database():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    ok, current, head = schema_status(engine)
    assert ok is False
    assert current is None
    assert head  # a revisão head existe e não é vazia


def test_schema_status_not_ready_after_create_all_without_alembic_stamp():
    """Regressão: o antigo init_db() no lifespan fazia só isto e passava por 'pronto'."""
    engine = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    ok, current, _head = schema_status(engine)
    assert ok is False
    assert current is None


def test_schema_status_ready_after_real_alembic_upgrade(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'ready.db').as_posix()}"
    _upgrade(monkeypatch, url)

    engine = make_engine(url)
    ok, current, head = schema_status(engine)
    assert ok is True
    assert current == head


def test_schema_status_not_ready_when_outdated(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'old.db').as_posix()}"
    _upgrade(monkeypatch, url, "20260916_01")  # revisão anterior à mais recente

    engine = make_engine(url)
    ok, current, head = schema_status(engine)
    assert ok is False
    assert current == "20260916_01"
    assert current != head


def test_ensure_database_ready_raises_for_missing_schema():
    engine = make_engine("sqlite://", poolclass=StaticPool)
    with pytest.raises(DatabaseNotReady, match="não inicializado"):
        ensure_database_ready(engine)


def test_ensure_database_ready_raises_for_outdated_schema(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'old2.db').as_posix()}"
    _upgrade(monkeypatch, url, "20260916_01")

    with pytest.raises(DatabaseNotReady, match="desatualizado"):
        ensure_database_ready(make_engine(url))


def test_ensure_database_ready_passes_silently_when_ready(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'ready2.db').as_posix()}"
    _upgrade(monkeypatch, url)

    ensure_database_ready(make_engine(url))  # não levanta


def test_health_endpoint_returns_503_for_outdated_schema(tmp_path, monkeypatch):
    """Achado na auditoria de pré-release: só a função schema_status tinha teste; o endpoint
    HTTP /health (o que start.ps1/monitoramento realmente batem) nunca tinha sido exercitado
    neste caminho de erro."""
    from fastapi.testclient import TestClient

    from app import main

    url = f"sqlite:///{(tmp_path / 'outdated.db').as_posix()}"
    _upgrade(monkeypatch, url, "20260916_01")
    engine = make_engine(url)

    def override():
        from sqlalchemy.orm import Session as _Session

        with _Session(engine) as session:
            yield session

    monkeypatch.setattr(main, "ensure_database_ready", lambda *a, **k: None)
    from app.database import get_db

    main.app.dependency_overrides[get_db] = override
    try:
        with TestClient(main.app) as client:
            resp = client.get("/health")
            assert resp.status_code == 503
            assert resp.json() == {"status": "unavailable", "reason": "database_schema_outdated"}
    finally:
        main.app.dependency_overrides.clear()
