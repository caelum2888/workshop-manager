"""Backup consistente do SQLite (API de backup do sqlite3, não cópia de arquivo aberto).

Uso:
    python scripts/db_backup.py                    # backups/oficinas-AAAAMMDD-HHMMSS.db
    python scripts/db_backup.py --prefix pre-migracao   # backups/pre-migracao-AAAAMMDD-HHMMSS.db

Sai com código != 0 (e mensagem no stderr) se o banco não existir, não for
SQLite, ou o backup reprovar em PRAGMA integrity_check.
"""

import argparse
import datetime as dt
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR, DATABASE_URL  # noqa: E402


def sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.removeprefix("sqlite:///"))


def _integrity_check(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return str(exc)  # ex.: "file is not a database" — não é "ok", tratado como falha abaixo
    finally:
        conn.close()


def backup(src: Path, backups_dir: Path, prefix: str | None = None) -> Path:
    if not src.exists():
        raise SystemExit(f"Banco de dados não encontrado em {src}. Nada para copiar.")
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{prefix}-{stamp}.db" if prefix else f"oficinas-{stamp}.db"
    dest = backups_dir / name

    src_conn = sqlite3.connect(str(src))
    dest_conn = sqlite3.connect(str(dest))
    try:
        src_conn.backup(dest_conn)  # consistente mesmo com o banco em uso; não é cópia de bytes
        result = "ok"
    except sqlite3.DatabaseError as exc:
        result = str(exc)
    finally:
        dest_conn.close()
        src_conn.close()

    if result == "ok":
        result = _integrity_check(dest)
    if result != "ok":
        dest.unlink(missing_ok=True)
        raise SystemExit(f"Backup falhou na verificação de integridade: {result}")
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", default=None, help='Ex.: "pre-migracao"')
    args = parser.parse_args()

    src = sqlite_path_from_url(DATABASE_URL)
    if src is None:
        raise SystemExit(
            "Backup automático só é suportado para SQLite local "
            "(DATABASE_URL atual não é sqlite:///...). Para PostgreSQL, use pg_dump."
        )
    dest = backup(src, BASE_DIR / "backups", prefix=args.prefix)
    print(f"Backup criado e verificado: {dest}")


if __name__ == "__main__":
    main()
