import pytest

from app import main, worker
from app.db import SessionLocal
from app.models import Notification, Satellite, Subscription, User
from test_orbits import START
from test_worker import seed
from sqlalchemy import select


@pytest.mark.parametrize('lang, title, body_part', [
    ('zh-CN', 'SPA 测试通知', '卫星过境提醒'),
    ('en', 'SPA Test Notification', 'Satellite pass alert'),
    ('ja', 'SPA テスト通知', '衛星パス通知'),
])
def test_test_push_uses_saved_language(admin, monkeypatch, lang, title, body_part):
    calls = []

    def send(secret, subject, body, only_channel):
        calls.append((subject, body, only_channel))
        return {'sent': 1, 'errors': []}

    monkeypatch.setattr(main, 'dispatch_notifications', send)
    response = admin.put('/api/v1/me/notify', json={'lang': lang})
    assert response.status_code == 200 and response.json()['lang'] == lang
    assert admin.get('/api/v1/me').json()['lang'] == lang
    assert admin.put('/api/v1/me/bark', json={'key': 'test-device'}).status_code == 200
    assert admin.post('/api/v1/me/notify/test', json={'channel': 'bark'}).json()['sent'] == 1
    [(actual_title, body, channel)] = calls
    assert actual_title == title and body_part in body and channel == 'bark'
    assert admin.put('/api/v1/me/bark', json={'key': ''}).json() == {'configured': False}
    assert admin.get('/api/v1/me').json()['lang'] == lang


def test_queued_notifications_follow_language_changes(admin):
    now = START.replace(tzinfo=None)
    with SessionLocal() as db:
        user = seed(db)
        db.get(Satellite, 25544).amsat_status = 'Sat/Mode Active（2小时前报告）'
        db.commit()
        user_id = user.id
    admin_id = admin.get('/api/v1/me').json()['id']
    with SessionLocal.begin() as db:
        db.scalar(select(Subscription).where(Subscription.user_id == user_id)).user_id = admin_id
        admin_user = db.get(User, admin_id)
        admin_user.latitude, admin_user.longitude, admin_user.altitude_m = 30.6, 104.1, 500
    assert admin.put('/api/v1/me/bark', json={'key': 'local-device'}).status_code == 200
    for lang, title, report in [
        ('zh-CN', '即将过境', '2小时前报告'),
        ('en', 'Upcoming Pass', '2h ago'),
        ('ja', 'パス接近', '2時間前に報告'),
    ]:
        assert admin.put('/api/v1/me/notify', json={'lang': lang}).status_code == 200
        with SessionLocal() as db:
            worker.plan(db, now)
            pending = db.scalar(select(Notification).where(Notification.user_id == admin_id))
            assert title in pending.title and report in pending.body
    with SessionLocal() as db:
        pending = db.scalar(select(Notification).where(Notification.user_id == admin_id))
        time = pending.due_at
        sent = []
        worker.deliver(db, time, lambda secret, title, body: sent.append((title, body)), clock=lambda: time)
        assert len(sent) == 1 and 'パス接近' in sent[0][0] and '2時間前に報告' in sent[0][1]
