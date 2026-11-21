import httpx
from .config import settings


class PushError(Exception):
    pass


def send_bark(key, title, body, transport=None):
    # Deployment-controlled origin; no per-user URLs or redirects (SSRF).
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            response = client.post(settings.bark_origin + '/push', json={
                'device_key': key, 'title': title, 'body': body, 'group': 'SPA', 'isArchive': '1'})
            response.raise_for_status()
            if response.json().get('code') != 200:
                raise PushError('Bark 返回失败状态')
    except (httpx.HTTPError, ValueError):
        raise PushError('Bark 网络或响应错误') from None
