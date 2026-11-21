import os
import tempfile
from pathlib import Path

# A dedicated database only; never use the deployment database for tests.
temp = tempfile.TemporaryDirectory()
os.environ['DATABASE_URL'] = os.getenv('TEST_DATABASE_URL', 'sqlite:///' + str(Path(temp.name) / 'test.db'))
os.environ['APP_SECRET_KEY'] = 'test-only-secret-at-least-thirty-two-characters'
os.environ['ADMIN_PASSWORD'] = 'test-admin-password'
os.environ['WORKER_ENABLED'] = 'false'

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from app.db import engine, Base
from app.main import app, attempts

@pytest.fixture(autouse=True)
def database():
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        from sqlalchemy import text
        conn.execute(text('DROP TABLE IF EXISTS alembic_version'))
    command.upgrade(Config('alembic.ini'), 'head')
    attempts.clear()
    yield

@pytest.fixture
def client():
    with TestClient(app) as client:
        client.headers['X-SPA-Request'] = '1'
        yield client

@pytest.fixture
def admin(client):
    r = client.post('/api/v1/auth/login', json={'username':'admin','password':'test-admin-password'})
    assert r.status_code == 200
    return client


def pytest_sessionfinish(session, exitstatus):
    engine.dispose()
    temp.cleanup()
