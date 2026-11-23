import json
from urllib.parse import parse_qs
import httpx
import pytest
from app.bark import normalize_bark_key, normalize_serverchan_key, validate_bark_origin, dispatch_notifications, PushError

KEY = 'SCT000000TestOnly'
URL = 'https://sctapi.ftqq.com/' + KEY + '.send?title=old&desp=old'

def test_protocols_are_separate():
    assert normalize_serverchan_key(URL) == KEY
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.host == 'sctapi.ftqq.com':
            assert req.url.path == '/' + KEY + '.send'
            assert not req.url.query
            assert parse_qs(req.content.decode()) == {'title':['new'], 'desp':['body']}
            return httpx.Response(200, json={'code':0})
        assert req.url.path == '/push'
        assert json.loads(req.content)['device_key'] == 'test-bark-key'
        return httpx.Response(200, json={'code':200})
    cfg = {'bark':{'enabled':True,'key':'test-bark-key'},'serverchan':{'enabled':True,'sendkey':URL}}
    assert dispatch_notifications(json.dumps(cfg), 'new', 'body', transport=httpx.MockTransport(handler))['sent'] == 2
    assert len(calls) == 2

@pytest.mark.parametrize('value', [URL, KEY])
def test_bark_rejects_serverchan(value):
    with pytest.raises(PushError, match='Server酱'):
        normalize_bark_key(value)

@pytest.mark.parametrize('value', [URL, 'https://sctapi.ftqq.com', 'https://api.day.app?key=secret'])
def test_origin_error_never_exposes_secret(value):
    with pytest.raises(PushError) as exc:
        validate_bark_origin(value)
    assert KEY not in str(exc.value)
    assert 'secret' not in str(exc.value)

def test_serverchan_rejects_other_hosts():
    with pytest.raises(PushError):
        normalize_serverchan_key('https://sctapi.ftqq.com.evil.example/' + KEY)
    with pytest.raises(PushError):
        normalize_serverchan_key('https://api.day.app/device')

def test_bark_url_matches_configured_server():
    assert normalize_bark_key('https://api.day.app/test-bark-key') == 'test-bark-key'
    with pytest.raises(PushError):
        normalize_bark_key('https://different.example/test-bark-key')

def test_save_validation_is_atomic(admin):
    assert admin.put('/api/v1/me/notify', json={'serverchan':{'enabled':True,'sendkey':URL}}).status_code == 200
    r = admin.put('/api/v1/me/notify',json={'bark':{'enabled':True,'key':URL},'serverchan':{'clear':True}})
    assert r.status_code == 422
    assert KEY not in r.text
    assert admin.get('/api/v1/me').json()['notify_channels']['serverchan']['configured']
    assert admin.put('/api/v1/me/notify',json={'serverchan':{'sendkey':'invalid'}}).status_code == 422
    assert admin.put('/api/v1/me/notify',json={'serverchan':{'sendkey':123}}).status_code == 422
