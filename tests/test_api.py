import json

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
        assert json.loads(cipher().decrypt(user.bark_key.encode())) == {
            'bark': {'enabled': True, 'key': 'device_secret'},
        }
    assert 'device_secret' not in admin.get('/api/v1/me').text
    assert admin.put('/api/v1/me/bark',json={'key':''}).json() == {'configured':False}
    assert admin.post('/api/v1/me/bark/test').status_code == 400


def test_multichannel_updates_preserve_legacy_bark_and_mask_credentials(admin):
    with SessionLocal.begin() as db:
        user = db.scalar(select(User).where(User.username == 'admin'))
        user.bark_key = cipher().encrypt(b'legacy_device_key').decode()

    response = admin.put('/api/v1/me/notify', json={
        'pushplus': {'enabled': True, 'token': 'private_pushplus_token', 'topic': 'field'},
    })
    assert response.status_code == 200
    channels = response.json()['notify_channels']
    assert channels['bark'] == {'enabled': True, 'configured': True}
    assert channels['pushplus'] == {'enabled': True, 'configured': True, 'topic': 'field'}

    response = admin.put('/api/v1/me/notify', json={'pushplus': {'enabled': False, 'topic': 'home'}})
    assert response.status_code == 200
    assert response.json()['notify_channels']['pushplus'] == {
        'enabled': False, 'configured': True, 'topic': 'home',
    }
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == 'admin'))
        assert json.loads(cipher().decrypt(user.bark_key.encode())) == {
            'bark': {'enabled': True, 'key': 'legacy_device_key'},
            'pushplus': {'enabled': False, 'token': 'private_pushplus_token', 'topic': 'home'},
        }
    profile = admin.get('/api/v1/me')
    assert profile.json()['notify_channels'] == response.json()['notify_channels']
    for secret in ('legacy_device_key', 'private_pushplus_token'):
        assert secret not in response.text
        assert secret not in profile.text

    response = admin.put('/api/v1/me/notify', json={'bark': {'clear': True}, 'pushplus': {'enabled': True}})
    assert response.status_code == 200
    assert response.json()['notify_channels']['bark'] == {'enabled': False, 'configured': False}
    assert response.json()['notify_channels']['pushplus'] == {
        'enabled': True, 'configured': True, 'topic': 'home',
    }
    assert admin.get('/api/v1/me').json()['bark_configured'] is True


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
