from datetime import timedelta
from sqlalchemy import select
import httpx
import pytest
from app.bark import send_bark, PushError
from app.db import SessionLocal, utcnow
from app.models import User, Satellite, Subscription, Notification
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
        deliver(db,n.due_at,lambda *args:calls.append(args))
        deliver(db,n.due_at+timedelta(seconds=1),lambda *args:calls.append(args))
        assert len(calls)==1
        assert n.state=='sent'


def test_retry_and_expiration():
    with SessionLocal() as db:
        seed(db)
        now=START.replace(tzinfo=None)
        plan(db,now)
        n=db.scalar(select(Notification).order_by(Notification.aos))
        def fail(*args): raise PushError('fail')
        deliver(db,n.due_at,fail)
        assert n.state=='pending' and n.attempts==1
        deliver(db,n.due_at,fail)
        assert n.attempts==2
        deliver(db,n.due_at,fail)
        assert n.state=='failed' and n.attempts==3
        n.state='pending'
        db.commit()
        deliver(db,n.aos,fail)
        assert n.state=='expired'


def test_disabled_subscription_never_sends():
    with SessionLocal() as db:
        seed(db)
        plan(db,START.replace(tzinfo=None))
        n=db.scalar(select(Notification).order_by(Notification.aos))
        db.scalar(select(Subscription)).enabled=False
        db.commit()
        deliver(db,n.due_at,lambda *args:pytest.fail('must not send'))
        assert n.state=='cancelled'


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
        deliver(db,n.due_at,fail)
        retry_at=n.due_at
        plan(db,retry_at-timedelta(seconds=30))
        assert n.due_at==retry_at
        plan(db,now+timedelta(days=30))
        assert n.state=='cancelled'
