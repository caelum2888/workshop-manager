"""SECRET_KEY nunca cai silenciosamente num valor conhecido/padrão fora dos testes.

Só quem usa cookies de sessão (app/main.py, via require_secret_key()) precisa
da chave: importar app.config sozinho (scripts de backup, create_admin, seed)
não deve falhar por causa dela.

Roda em subprocessos limpos: a detecção de "estamos em teste" é
`"pytest" in sys.modules`, que só é verdadeira dentro do processo do pytest.
Define SECRET_KEY="" (não apenas remove a variável) para o cenário "ausente"
não depender de um .env real do projeto poder ou não existir neste disco.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(code: str, secret_key: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "SECRET_KEY": secret_key, "APP_ENV": "development"}
    return subprocess.run(
        [sys.executable, "-c", code], cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=30,
    )


def test_importing_config_alone_never_fails_regardless_of_secret_key():
    """Scripts que só querem DATABASE_URL/BASE_DIR (backup, create_admin, seed) não usam sessão."""
    result = _run("import app.config; print('IMPORT_OK')", "")
    assert result.returncode == 0
    assert "IMPORT_OK" in result.stdout


def test_missing_secret_key_fails_clearly_when_required():
    result = _run("import app.config; app.config.require_secret_key()", "")
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr
    assert "dev-only-change-me" not in result.stderr  # o fallback previsível não existe mais


def test_short_secret_key_is_rejected():
    result = _run("import app.config; app.config.require_secret_key()", "short-key")
    assert result.returncode != 0
    assert "SECRET_KEY" in result.stderr


def test_strong_secret_key_is_accepted():
    result = _run("import app.config; app.config.require_secret_key(); print('OK')", "x" * 40)
    assert result.returncode == 0
    assert "OK" in result.stdout
