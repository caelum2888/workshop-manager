"""Backup e restauração do SQLite: consistentes, verificados, sem sobrescrever em silêncio."""

import sqlite3

import pytest

from scripts.db_backup import backup
from scripts.db_restore import restore


def _make_db(path, value="original"):
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()


def _read(path):
    conn = sqlite3.connect(str(path))
    try:
        return conn.execute("SELECT v FROM t").fetchone()[0]
    finally:
        conn.close()


# ------------------------------------------------------------------- backup
def test_backup_creates_verified_timestamped_file(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    dest_dir = tmp_path / "backups"

    dest = backup(src, dest_dir)

    assert dest.exists() and dest.parent == dest_dir
    assert dest.name.startswith("oficinas-") and dest.name.endswith(".db")
    assert _read(dest) == "original"


def test_backup_pre_migration_prefix(tmp_path):
    src = tmp_path / "src.db"
    _make_db(src)
    dest = backup(src, tmp_path / "backups", prefix="pre-migracao")
    assert dest.name.startswith("pre-migracao-")


def test_backup_missing_source_fails_loudly(tmp_path):
    with pytest.raises(SystemExit):
        backup(tmp_path / "nao-existe.db", tmp_path / "backups")


def test_backup_rejects_corrupt_source_and_removes_partial_file(tmp_path):
    src = tmp_path / "bad.db"
    src.write_bytes(b"isto nao e um banco sqlite valido, so bytes aleatorios")
    dest_dir = tmp_path / "backups"
    with pytest.raises(SystemExit, match="integridade"):
        backup(src, dest_dir)
    assert list(dest_dir.glob("*.db")) == []  # nada de arquivo quebrado deixado para trás


# ----------------------------------------------------------------- restore
def test_restore_preserves_current_database_before_overwriting(tmp_path):
    dest = tmp_path / "current.db"
    _make_db(dest, value="atual")
    backup_file = tmp_path / "backup.db"
    _make_db(backup_file, value="do backup")

    preserved = restore(backup_file, dest)

    assert preserved is not None and preserved.exists()
    assert ".antes-de-restaurar-" in preserved.name
    assert _read(preserved) == "atual"
    assert _read(dest) == "do backup"


def test_restore_with_no_existing_database_does_not_preserve_anything(tmp_path):
    dest = tmp_path / "current.db"  # não existe ainda
    backup_file = tmp_path / "backup.db"
    _make_db(backup_file)

    preserved = restore(backup_file, dest)

    assert preserved is None
    assert _read(dest) == "original"


def test_restore_rejects_missing_backup(tmp_path):
    with pytest.raises(SystemExit, match="não encontrado"):
        restore(tmp_path / "nao-existe.db", tmp_path / "current.db")


def test_restore_rejects_corrupt_backup_and_leaves_current_database_untouched(tmp_path):
    dest = tmp_path / "current.db"
    _make_db(dest, value="intacto")
    bad_backup = tmp_path / "bad.db"
    bad_backup.write_bytes(b"lixo, nao e sqlite")

    with pytest.raises(SystemExit, match="integridade"):
        restore(bad_backup, dest)

    assert _read(dest) == "intacto"  # nada foi sobrescrito
