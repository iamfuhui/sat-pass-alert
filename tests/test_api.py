from sqlalchemy import select
from app.db import SessionLocal
from app.models import User
from app.security import cipher


def test_health_and_closed_registration(client):
    assert client.get('/healthz').json()['status'] == 'ok'
    assert client.get('/api/v1/me').status_code == 401
    assert client.post('/api/v1/auth/register',json={'username':'alice','password':'long-password-123'}).status_code == 403


def test_csrf(admin):
    assert admin.put('/api/v1/me/bark',json={'key':'secret'},headers={'X-SPA-Request':''}).status_code == 403
    assert admin.put('/api/v1/me/bark',json={'key':'secret'},headers={'Sec-Fetch-Site':'cross-site'}).status_code == 403


def test_invite_single_use_and_user_isolation(admin):
    assert admin.put('/api/v1/admin/registration',json={'mode':'invite'}).status_code == 200
    invite=admin.post('/api/v1/admin/invites').json()['invite']
    assert admin.post('/api/v1/auth/register',json={'username':'alice','password':'long-password-123','invite':invite}).json()['approved']
    assert admin.post('/api/v1/auth/register',json={'username':'bob','password':'long-password-123','invite':invite}).status_code == 400
    admin.put('/api/v1/me/bark',json={'key':'admin-secret-key'})
    admin.post('/api/v1/auth/logout')
    assert admin.post('/api/v1/auth/login',json={'username':'alice','password':'long-password-123'}).status_code == 200
    assert admin.get('/api/v1/admin/users').status_code == 403
    assert not admin.get('/api/v1/me').json()['bark_configured']
    assert admin.get('/api/v1/notifications').json() == []
    assert admin.get('/api/v1/subscriptions').json() == []


def test_approval_and_disable(admin):
    admin.put('/api/v1/admin/registration',json={'mode':'approval'})
    assert not admin.post('/api/v1/auth/register',json={'username':'alice','password':'long-password-123'}).json()['approved']
    assert admin.post('/api/v1/auth/login',json={'username':'alice','password':'long-password-123'}).status_code == 401
    uid=next(u['id'] for u in admin.get('/api/v1/admin/users').json() if u['username']=='alice')
    admin.put(f'/api/v1/admin/users/{uid}/approval',json={'approved':True})
    assert admin.post('/api/v1/auth/login',json={'username':'alice','password':'long-password-123'}).status_code == 200


def test_profile_validation_and_key_encryption(admin):
    assert admin.put('/api/v1/me',json={'latitude':91,'longitude':0}).status_code == 422
    assert admin.put('/api/v1/me',json={'latitude':30,'longitude':104,'timezone':'No/SuchZone'}).status_code == 422
    assert admin.put('/api/v1/me',json={'latitude':30.6,'longitude':104.1,'altitude_m':500,'timezone':'Asia/Shanghai'}).status_code == 200
    assert admin.put('/api/v1/me/bark',json={'key':'device_secret'}).status_code == 200
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.username=='admin'))
        assert user.bark_key != 'device_secret'
        assert cipher().decrypt(user.bark_key.encode()).decode() == 'device_secret'
    assert 'device_secret' not in admin.get('/api/v1/me').text
    assert admin.put('/api/v1/me/bark',json={'key':''}).json() == {'configured':False}
    assert admin.post('/api/v1/me/bark/test').status_code == 400


def test_validation_and_auth_rate_limit(client):
    assert client.get('/api/v1/passes?hours=10000').status_code == 401
    for _ in range(10):
        assert client.post('/api/v1/auth/login',json={'username':'nobody','password':'long-password-123'}).status_code == 401
    assert client.post('/api/v1/auth/login',json={'username':'nobody','password':'long-password-123'}).status_code == 429


def test_unknown_api_stays_404(client):
    assert client.get('/api/v1/not-a-route').status_code == 404


def test_import_subscribe_predict_and_isolate(admin):
    import json
    from test_orbits import OMM, START
    from unittest.mock import patch
    r=admin.post('/api/v1/admin/satellites/import',json={'elements':json.dumps(OMM),'format':'omm'})
    assert r.status_code==200
    assert admin.put('/api/v1/subscriptions/25544',json={'min_elevation':10,'lead_minutes':10}).status_code==200
    admin.put('/api/v1/me',json={'latitude':30.6,'longitude':104.1,'altitude_m':500})
    with patch('app.main.utcnow',return_value=START.replace(tzinfo=None)):
        r=admin.get('/api/v1/passes')
    assert r.status_code==200 and len(r.json()['passes'])>0
    assert admin.get('/api/v1/passes?hours=1000').status_code==422
    admin.put('/api/v1/admin/registration',json={'mode':'approval'})
    admin.post('/api/v1/auth/register',json={'username':'second','password':'long-password-123'})
    uid=next(u['id'] for u in admin.get('/api/v1/admin/users').json() if u['username']=='second')
    admin.put(f'/api/v1/admin/users/{uid}/approval',json={'approved':True})
    admin.post('/api/v1/auth/logout')
    admin.post('/api/v1/auth/login',json={'username':'second','password':'long-password-123'})
    assert admin.get('/api/v1/subscriptions').json()==[]
    assert admin.get('/api/v1/passes').json()['passes']==[]
