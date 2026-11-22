from datetime import timedelta
from sqlalchemy import select
import httpx
import pytest
from app.bark import send_bark, PushError
from app.db import SessionLocal
from app.models import User, Subscription, Notification
from app.security import cipher
from app.worker import plan, deliver
from app.catalog import save_satellite
from test_orbits import OMM, START
import json


def seed(db):
    user=User(username='tester',password_hash='unused',approved=True,bark_key=cipher().encrypt(b'key').decode(),latitude=30.6,longitude=104.1,altitude_m=500)
    db.add(user)
    save_satellite(db,json.dumps(OMM),'omm')
    db.flush()
    db.add(Subscription(user_id=user.id,norad_id=25544,min_elevation=0,lead_minutes=10))
    db.commit()
    return user


def deliver_at(db, now, sender):
    deliver(db, now, sender, clock=lambda: now)


def test_planning_deduplicates_jitter_and_sends_once():
    with SessionLocal() as db:
        seed(db)
        now=START.replace(tzinfo=None)
        plan(db,now)
        first=db.scalars(select(Notification).order_by(Notification.aos)).all()
        assert first
        count=len(first)
        plan(db,now+timedelta(seconds=30))
        assert len(db.scalars(select(Notification)).all()) == count
        n=first[0]
        calls=[]
        deliver_at(db,n.due_at,lambda *args:calls.append(args))
        deliver_at(db,n.due_at+timedelta(seconds=1),lambda *args:calls.append(args))
        assert len(calls)==1
        assert n.state=='sent'


def test_retry_and_expiration():
    with SessionLocal() as db:
        seed(db)
        now=START.replace(tzinfo=None)
        plan(db,now)
        n=db.scalar(select(Notification).order_by(Notification.aos))
        def fail(*args): raise PushError('fail')
        deliver_at(db,n.due_at,fail)
        assert n.state=='pending' and n.attempts==1
        deliver_at(db,n.due_at,fail)
        assert n.attempts==2
        deliver_at(db,n.due_at,fail)
        assert n.state=='failed' and n.attempts==3
        n.state='pending'
        db.commit()
        deliver_at(db,n.aos,fail)
        assert n.state=='expired'


def test_disabled_subscription_never_sends():
    with SessionLocal() as db:
        seed(db)
        plan(db,START.replace(tzinfo=None))
        n=db.scalar(select(Notification).order_by(Notification.aos))
        db.scalar(select(Subscription)).enabled=False
        db.commit()
        deliver_at(db,n.due_at,lambda *args:pytest.fail('must not send'))
        assert n.state=='cancelled'


def test_slow_send_expires_next_reminder_at_aos_and_records_completion():
    with SessionLocal() as db:
        user = seed(db)
        now = START.replace(tzinfo=None)
        first = Notification(user_id=user.id, norad_id=25544, title='first', body='test',
            due_at=now - timedelta(seconds=2), aos=now + timedelta(seconds=3))
        second = Notification(user_id=user.id, norad_id=25544, title='second', body='test',
            due_at=now - timedelta(seconds=1), aos=now + timedelta(seconds=8))
        db.add_all([first, second])
        db.commit()
        calls = []

        def send(secret, title, body):
            nonlocal now
            calls.append(title)
            now += timedelta(seconds=8)

        deliver(db, now, send, clock=lambda: now)

        assert calls == ['first']
        assert first.state == 'sent' and first.sent_at == now
        assert second.state == 'expired' and second.attempts == 0


def test_claim_crossing_aos_does_not_start_send():
    with SessionLocal() as db:
        user = seed(db)
        now = START.replace(tzinfo=None)
        notification = Notification(user_id=user.id, norad_id=25544, title='test', body='test',
            due_at=now, aos=now + timedelta(seconds=1))
        db.add(notification)
        db.commit()

        times = iter((now, notification.aos))
        deliver(db, now, lambda *args: pytest.fail('must not send'), clock=times.__next__)

        assert notification.state == 'expired' and notification.attempts == 0


def test_retry_is_scheduled_from_failure_completion():
    with SessionLocal() as db:
        seed(db)
        plan(db, START.replace(tzinfo=None))
        notification = db.scalar(select(Notification).order_by(Notification.aos))
        now = notification.due_at

        def fail(*args):
            nonlocal now
            now += timedelta(seconds=8)
            raise PushError('test')

        deliver(db, now, fail, clock=lambda: now)

        assert notification.state == 'pending'
        assert notification.due_at == now + timedelta(minutes=2)


def test_bark_payload_and_failures():
    def success(request):
        payload=json.loads(request.content)
        assert request.url.path=='/push'
        assert payload['device_key']=='key'
        assert 'key' not in str(request.url)
        assert payload['title']=='title'
        return httpx.Response(200,json={'code':200})
    send_bark('key','title','body',httpx.MockTransport(success))
    for response in [httpx.Response(500),httpx.Response(200,json={'code':400}),httpx.Response(200,text='invalid')]:
        with pytest.raises(PushError):
            send_bark('key','title','body',httpx.MockTransport(lambda request:response))


def test_replanning_preserves_retry_backoff_and_cancels_stale_orbits():
    with SessionLocal() as db:
        seed(db)
        now=START.replace(tzinfo=None)
        plan(db,now)
        n=db.scalar(select(Notification).order_by(Notification.aos))
        def fail(*args): raise PushError('fail')
        deliver_at(db,n.due_at,fail)
        retry_at=n.due_at
        plan(db,retry_at-timedelta(seconds=30))
        assert n.due_at==retry_at
        plan(db,now+timedelta(days=30))
        assert n.state=='cancelled'
