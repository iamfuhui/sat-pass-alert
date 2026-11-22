import ipaddress
import json
import re
import socket
from datetime import datetime, timezone
from urllib.parse import urlsplit
import httpx
from .config import settings


class PushError(Exception):
    pass


def normalize_bark_key(raw: str) -> str:
    val = (raw or '').strip()
    if val.startswith(('http://', 'https://')):
        parts = [p for p in urlsplit(val).path.split('/') if p]
        if parts:
            val = parts[0]
    return val


def validate_public_url(raw_url: str, label: str = '推送地址') -> str:
    url = (raw_url or '').strip()
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise PushError(f'{label}必须为有效的 http/https 地址')
    host = parsed.hostname.lower()
    if host in ('localhost', '127.0.0.1', '::1', '0.0.0.0') or host.endswith('.local'):
        raise PushError(f'{label}不允许指向本地回环地址')
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == 'https' else 80))
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                raise PushError(f'{label}不允许指向内网或保留地址')
    except socket.gaierror:
        raise PushError(f'{label}域名无法解析') from None
    return url.rstrip('/')


def parse_channels(decrypted_secret: str | None) -> dict:
    if not decrypted_secret:
        return {}
    s = decrypted_secret.strip()
    if not s:
        return {}
    if s.startswith('{'):
        try:
            data = json.loads(s)
            if isinstance(data, dict):
                return data
        except ValueError:
            pass
    return {'bark': {'enabled': True, 'key': normalize_bark_key(s)}}


def has_configured_channel(decrypted_secret: str | None) -> bool:
    ch = parse_channels(decrypted_secret)
    if ch.get('bark', {}).get('enabled') and ch['bark'].get('key'):
        return True
    if ch.get('serverchan', {}).get('enabled') and ch['serverchan'].get('sendkey'):
        return True
    if ch.get('pushplus', {}).get('enabled') and ch['pushplus'].get('token'):
        return True
    if ch.get('telegram', {}).get('enabled') and ch['telegram'].get('bot_token') and ch['telegram'].get('chat_id'):
        return True
    if ch.get('ntfy', {}).get('enabled') and ch['ntfy'].get('topic'):
        return True
    if ch.get('bot_webhook', {}).get('enabled') and ch['bot_webhook'].get('url'):
        return True
    if ch.get('webhook', {}).get('enabled') and ch['webhook'].get('url'):
        return True
    return False


def mask_channels_summary(decrypted_secret: str | None) -> dict:
    ch = parse_channels(decrypted_secret)
    return {
        'bark': {'enabled': bool(ch.get('bark', {}).get('enabled')), 'configured': bool(ch.get('bark', {}).get('key'))},
        'serverchan': {'enabled': bool(ch.get('serverchan', {}).get('enabled')), 'configured': bool(ch.get('serverchan', {}).get('sendkey'))},
        'pushplus': {
            'enabled': bool(ch.get('pushplus', {}).get('enabled')),
            'configured': bool(ch.get('pushplus', {}).get('token')),
            'topic': ch.get('pushplus', {}).get('topic', ''),
        },
        'telegram': {
            'enabled': bool(ch.get('telegram', {}).get('enabled')),
            'configured': bool(ch.get('telegram', {}).get('bot_token') and ch.get('telegram', {}).get('chat_id')),
            'chat_id': ch.get('telegram', {}).get('chat_id', ''),
        },
        'ntfy': {
            'enabled': bool(ch.get('ntfy', {}).get('enabled')),
            'configured': bool(ch.get('ntfy', {}).get('topic')),
            'server_url': ch.get('ntfy', {}).get('server_url', 'https://ntfy.sh'),
            'topic': ch.get('ntfy', {}).get('topic', ''),
        },
        'bot_webhook': {
            'enabled': bool(ch.get('bot_webhook', {}).get('enabled')),
            'configured': bool(ch.get('bot_webhook', {}).get('url')),
        },
        'webhook': {
            'enabled': bool(ch.get('webhook', {}).get('enabled')),
            'configured': bool(ch.get('webhook', {}).get('url')),
        },
    }


def send_bark(key, title, body, transport=None):
    clean_key = normalize_bark_key(key)
    if not clean_key:
        raise PushError('Bark 设备 Key 不能为空')
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            response = client.post(settings.bark_origin + '/push', json={
                'device_key': clean_key, 'title': title, 'body': body, 'group': 'SPA', 'isArchive': '1'})
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if response.status_code != 200 or payload.get('code') != 200:
                msg = payload.get('message') or f'HTTP {response.status_code}'
                raise PushError(f'Bark 返回错误：{msg}')
    except PushError:
        raise
    except httpx.HTTPError as exc:
        raise PushError(f'Bark 网络连接失败：{type(exc).__name__}') from None


def _send_serverchan(cfg: dict, title: str, body: str, transport=None):
    sendkey = (cfg.get('sendkey') or '').strip()
    if not re.match(r'^[A-Za-z0-9_-]{8,128}$', sendkey):
        raise PushError('Server酱 SendKey 格式无效')
    url = f'https://sctapi.ftqq.com/{sendkey}.send'
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            r = client.post(url, data={'title': title, 'desp': body})
            data = r.json() if r.content else {}
            if r.status_code != 200 or data.get('code') != 0:
                raise PushError(f"Server酱返回错误：{data.get('message') or r.status_code}")
    except PushError:
        raise
    except (httpx.HTTPError, ValueError):
        raise PushError('Server酱网络或响应错误') from None


def _send_pushplus(cfg: dict, title: str, body: str, transport=None):
    token = (cfg.get('token') or '').strip()
    if not token:
        raise PushError('PushPlus Token 不能为空')
    payload = {'token': token, 'title': title, 'content': body, 'template': 'txt'}
    if (cfg.get('topic') or '').strip():
        payload['topic'] = cfg['topic'].strip()
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            r = client.post('https://www.pushplus.plus/send', json=payload)
            data = r.json() if r.content else {}
            if r.status_code != 200 or data.get('code') != 200:
                raise PushError(f"PushPlus 返回错误：{data.get('msg') or r.status_code}")
    except PushError:
        raise
    except (httpx.HTTPError, ValueError):
        raise PushError('PushPlus 网络或响应错误') from None


def _send_telegram(cfg: dict, title: str, body: str, transport=None):
    token = (cfg.get('bot_token') or '').strip()
    chat_id = str(cfg.get('chat_id') or '').strip()
    if not re.match(r'^\d+:[A-Za-z0-9_-]+$', token) or not chat_id:
        raise PushError('Telegram Bot Token 或 Chat ID 格式无效')
    url = f'https://api.telegram.org/bot{token}/sendMessage'
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            r = client.post(url, json={'chat_id': chat_id, 'text': f'{title}\n\n{body}'})
            data = r.json() if r.content else {}
            if r.status_code != 200 or not data.get('ok'):
                raise PushError(f"Telegram 返回错误：{data.get('description') or r.status_code}")
    except PushError:
        raise
    except (httpx.HTTPError, ValueError):
        raise PushError('Telegram 网络或响应错误') from None


def _send_ntfy(cfg: dict, title: str, body: str, transport=None):
    base = validate_public_url(cfg.get('server_url') or 'https://ntfy.sh', 'ntfy 服务器地址')
    topic = (cfg.get('topic') or '').strip().lstrip('/')
    if not re.match(r'^[A-Za-z0-9_.-]{1,128}$', topic):
        raise PushError('ntfy Topic 格式无效')
    headers = {'Title': title.encode('utf-8').decode('latin-1', errors='ignore') or 'SPA Alert'}
    if (cfg.get('token') or '').strip():
        headers['Authorization'] = f"Bearer {cfg['token'].strip()}"
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            r = client.post(f'{base}/{topic}', content=body.encode('utf-8'), headers=headers)
            if r.status_code not in (200, 201, 204):
                raise PushError(f'ntfy 返回状态 {r.status_code}')
    except PushError:
        raise
    except httpx.HTTPError:
        raise PushError('ntfy 网络或响应错误') from None


def _send_bot_webhook(cfg: dict, title: str, body: str, transport=None):
    url = validate_public_url(cfg.get('url') or '', '群机器人 Webhook')
    host = urlsplit(url).hostname or ''
    text = f'{title}\n{body}'
    if 'feishu.cn' in host or 'larksuite.com' in host:
        payload = {'msg_type': 'text', 'content': {'text': text}}
    else:
        payload = {'msgtype': 'text', 'text': {'content': text}}
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            r = client.post(url, json=payload)
            if r.status_code != 200:
                raise PushError(f'群机器人返回状态 {r.status_code}')
    except PushError:
        raise
    except httpx.HTTPError:
        raise PushError('群机器人 Webhook 网络错误') from None


def _send_custom_webhook(cfg: dict, title: str, body: str, transport=None):
    url = validate_public_url(cfg.get('url') or '', '自定义 Webhook')
    headers = {'Content-Type': 'application/json'}
    if (cfg.get('token') or '').strip():
        headers['Authorization'] = f"Bearer {cfg['token'].strip()}"
    payload = {'title': title, 'body': body, 'timestamp': datetime.now(timezone.utc).isoformat()}
    try:
        with httpx.Client(timeout=15, follow_redirects=False, transport=transport) as client:
            r = client.post(url, json=payload, headers=headers)
            if r.status_code < 200 or r.status_code >= 300:
                raise PushError(f'Webhook 返回状态 {r.status_code}')
    except PushError:
        raise
    except httpx.HTTPError:
        raise PushError('自定义 Webhook 网络错误') from None


def dispatch_notifications(decrypted_secret: str | None, title: str, body: str, transport=None, only_channel: str | None = None):
    ch = parse_channels(decrypted_secret)
    handlers = [
        ('bark', lambda c: send_bark(c.get('key', ''), title, body, transport=transport)),
        ('serverchan', lambda c: _send_serverchan(c, title, body, transport=transport)),
        ('pushplus', lambda c: _send_pushplus(c, title, body, transport=transport)),
        ('telegram', lambda c: _send_telegram(c, title, body, transport=transport)),
        ('ntfy', lambda c: _send_ntfy(c, title, body, transport=transport)),
        ('bot_webhook', lambda c: _send_bot_webhook(c, title, body, transport=transport)),
        ('webhook', lambda c: _send_custom_webhook(c, title, body, transport=transport)),
    ]
    active = []
    for name, fn in handlers:
        cfg = ch.get(name) or {}
        if only_channel:
            if name == only_channel:
                active.append((name, cfg, fn))
        elif cfg.get('enabled'):
            active.append((name, cfg, fn))

    if not active:
        raise PushError('未启用任何推送通道')

    errors = []
    sent_count = 0
    for name, cfg, fn in active:
        try:
            fn(cfg)
            sent_count += 1
        except PushError as exc:
            errors.append(str(exc))

    if sent_count == 0:
        raise PushError('；'.join(errors))
    return {'sent': sent_count, 'errors': errors}
