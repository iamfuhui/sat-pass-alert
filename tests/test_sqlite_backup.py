from contextlib import closing
from io import BytesIO
import sqlite3

import pytest

from app.sqlite_backup import restore_backup, sqlite_path, write_backup


def test_sqlite_backup_includes_committed_wal_data_and_restores_to_new_volume(tmp_path):
    source = tmp_path / 'source.db'
    output = BytesIO()
    with closing(sqlite3.connect(source)) as db:
        db.execute('PRAGMA journal_mode=WAL')
        db.executescript("""
            CREATE TABLE alembic_version (version_num TEXT NOT NULL);
            INSERT INTO alembic_version VALUES ('f985e4a1bd00');
            CREATE TABLE subscriptions (norad_id INTEGER);
            INSERT INTO subscriptions VALUES (25544);
        """)
        db.commit()
        assert source.with_suffix('.db-wal').stat().st_size > 0
        write_backup(source, output)

    destination = tmp_path / 'new-volume' / 'spa.db'
    destination.parent.mkdir()
    restore_backup(destination, BytesIO(output.getvalue()))
    with closing(sqlite3.connect(destination)) as db:
        assert db.execute('SELECT norad_id FROM subscriptions').fetchall() == [(25544,)]

    with pytest.raises(FileExistsError, match='already exists'):
        restore_backup(destination, BytesIO(b'corrupt payload'))
    assert destination.stat().st_size == len(output.getvalue())


def test_invalid_backup_leaves_destination_empty(tmp_path):
    destination = tmp_path / 'spa.db'
    with pytest.raises(sqlite3.DatabaseError):
        restore_backup(destination, BytesIO(b'not a database'))
    assert not destination.exists()


def test_backup_requires_a_file_based_sqlite_url():
    with pytest.raises(ValueError, match='file-based SQLite'):
        sqlite_path('sqlite:///:memory:')
    with pytest.raises(ValueError, match='file-based SQLite'):
        sqlite_path('mysql+pymysql://spa:secret@db/spa')
