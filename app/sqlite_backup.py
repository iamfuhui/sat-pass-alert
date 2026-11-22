"""Stream a SQLite backup or restore it into an empty SPA data volume."""

import argparse
from contextlib import closing
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory

from sqlalchemy.engine import make_url

from .config import settings


def sqlite_path(database_url):
    url = make_url(database_url)
    if url.get_backend_name() != 'sqlite' or url.database in (None, '', ':memory:'):
        raise ValueError('A file-based SQLite DATABASE_URL is required')
    return Path(url.database).resolve()


def write_backup(path, output):
    with TemporaryDirectory() as scratch:
        snapshot = Path(scratch) / 'snapshot.db'
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(snapshot)) as target:
                source.backup(target)
        with snapshot.open('rb') as file:
            shutil.copyfileobj(file, output)


def restore_backup(path, source):
    if path.exists():
        raise FileExistsError('Destination database already exists; use a new data volume')
    # Keep staging on the destination filesystem so publishing the validated file is atomic.
    with TemporaryDirectory(dir=path.parent, prefix='.restore-') as scratch:
        snapshot = Path(scratch) / 'snapshot.db'
        with snapshot.open('xb') as file:
            shutil.copyfileobj(source, file)
        with closing(sqlite3.connect(snapshot.as_uri() + '?mode=ro&immutable=1', uri=True)) as database:
            if database.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                raise ValueError('SQLite backup did not pass integrity_check')
            if database.execute('SELECT version_num FROM alembic_version').fetchone() is None:
                raise ValueError('Backup has no Alembic revision')
        os.chmod(snapshot, 0o600)
        os.link(snapshot, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('backup', 'restore'))
    action = parser.parse_args().action
    try:
        path = sqlite_path(settings.database_url)
        if action == 'backup':
            write_backup(path, sys.stdout.buffer)
        else:
            restore_backup(path, sys.stdin.buffer)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f'{action}: {exc}\n')


if __name__ == '__main__':
    main()
