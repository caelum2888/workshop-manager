"""Restaura um backup do SQLite, preservando o banco atual antes de trocar.

Uso:
    python scripts/db_restore.py backups/oficinas-20260921-183000.db

A seleção interativa (listar, escolher, confirmar) fica em scripts/restaurar.ps1;
este módulo só faz a operação de arquivo, validada, de forma testável.
"""

import argparse
import datetime as dt
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DATABASE_URL  # noqa: E402


def sqlite_path_from_url(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return Path(url.removeprefix("sqlite:///"))


def _integrity_check(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("PRAGMA integrity_check").fetchone()[0]
    except sqlite3.DatabaseError as exc:
        return str(exc)
    finally:
        conn.close()


def restore(backup_path: Path, dest_path: Path) -> Path | None:
    """Restaura backup_path para dest_path. Retorna o caminho do banco atual
    preservado (None se não havia banco atual)."""
    if not backup_path.exists():
        raise SystemExit(f"Backup não encontrado: {backup_path}")
    result = _integrity_check(backup_path)
    if result != "ok":
        raise SystemExit(f"Backup reprovado na verificação de integridade ({result}); restauração cancelada.")

    preserved = None
    if dest_path.exists():
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        preserved = dest_path.with_name(f"{dest_path.stem}.antes-de-restaurar-{stamp}{dest_path.suffix}")
        shutil.copy2(dest_path, preserved)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, dest_path)

    final = _integrity_check(dest_path)
    if final != "ok":
        if preserved:
            shutil.copy2(preserved, dest_path)  # reverte: o original preservado volta ao lugar
        raise SystemExit(f"Falha na verificação após restaurar ({final}); banco original preservado.")
    return preserved


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", help="Caminho do arquivo de backup a restaurar")
    parser.add_argument("--dest", default=None, help="Caminho do banco de destino (padrão: DATABASE_URL atual)")
    args = parser.parse_args()

    dest = Path(args.dest) if args.dest else sqlite_path_from_url(DATABASE_URL)
    if dest is None:
        raise SystemExit("Restauração automática só é suportada para SQLite local.")

    preserved = restore(Path(args.backup), dest)
    if preserved:
        print(f"Banco anterior preservado em: {preserved}")
    print(f"Banco restaurado a partir de: {args.backup}")


if __name__ == "__main__":
    main()
