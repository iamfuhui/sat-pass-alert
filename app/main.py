import json
import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
import httpx
from fastapi import FastAPI, Depends, HTTPException, Request, Response, Query
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session
from .config import settings
from .db import SessionLocal, get_db, utcnow
from .models import User, Invite, SystemSetting, Satellite, Subscription, Notification
from .security import hasher, verify_password, serializer, cipher, current_user, admin_user
from .schemas import Credentials, Registration, Profile, BarkConfig, SubscriptionInput, OrbitInput, FetchInput, SyncDefaultsInput, RegistrationMode, Approval
from .orbits import predict
from .catalog import save_satellite, refresh_one, sync_default_satellites
from .amsat import refresh_status_async
from .bark import dispatch_notifications, has_configured_channel, mask_channels_summary, normalize_bark_key, parse_channels, normalize_serverchan_key, validate_bark_origin, PushError
from .worker import get_user_lang, SUPPORTED_LANGS
from .worker import loop


@asynccontextmanager
async def lifespan(app):
    if len(settings.secret) < 32:
        raise RuntimeError('APP_SECRET_KEY must contain at least 32 characters')
    try:
        validate_bark_origin(settings.bark_origin)
    except PushError as exc:
        raise RuntimeError(str(exc)) from None
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.is_admin.is_(True))) is None:
            if len(settings.admin_password) < 12:
                raise RuntimeError('Set ADMIN_PASSWORD (at least 12 characters) for initial admin')
            db.add(User(username=settings.admin_username, password_hash=hasher.hash(settings.admin_password),
                is_admin=True, approved=True))
            db.commit()
    stop = threading.Event()
    worker = threading.Thread(target=loop, args=(stop,), daemon=True)
    def _bootstrap_catalog():
        try:
            with SessionLocal() as bdb:
                if sync_default_satellites(bdb, source='auto', only_missing=True):
                    bdb.commit()
            refresh_status_async()
        except Exception:
            pass

    if settings.worker_enabled:
        threading.Thread(target=_bootstrap_catalog, name='catalog-bootstrap', daemon=True).start()
        worker.start()
    yield
    stop.set()
    if settings.worker_enabled:
        worker.join(timeout=25)


app = FastAPI(title='SPA — Sat Pass Alert', version='0.1.0', lifespan=lifespan)
attempts = defaultdict(deque)
rate_lock = threading.Lock()


@app.middleware('http')
async def security_headers(request: Request, call_next):
    if request.url.path.startswith('/api/') and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        # Same-origin custom header protects cookie sessions even on plain HTTP.
        if request.headers.get('x-spa-request') != '1':
            return JSONResponse({'detail': '缺少请求安全标记'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '拒绝跨站请求'}, status_code=403)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'same-origin'
    if request.url.path.startswith('/api/') or not request.url.path.startswith('/assets/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def throttle(request, category, limit=10):
    key = (request.client.host if request.client else 'unknown', category)
    now = time.monotonic()
    with rate_lock:
        # Bound memory used by inactive clients.
        for old in list(attempts):
            if not attempts[old] or attempts[old][-1] < now - 300:
                del attempts[old]
        q = attempts[key]
        while q and q[0] < now - 300:
            q.popleft()
        if len(q) >= limit:
            raise HTTPException(429, '操作过于频繁，请五分钟后重试')
        q.append(now)


def _decrypt_notify(user: User) -> str | None:
    if not user.bark_key:
        return None
    try:
        return cipher().decrypt(user.bark_key.encode()).decode()
    except Exception:
        return None


def profile(user: User):
    secret_raw = _decrypt_notify(user)
    return {
        'id': user.id,
        'username': user.username,
        'is_admin': user.is_admin,
        'locator': user.locator,
        'latitude': user.latitude,
        'longitude': user.longitude,
        'altitude_m': user.altitude_m,
        'timezone': user.timezone,
        'bark_configured': has_configured_channel(secret_raw),
        'notify_channels': mask_channels_summary(secret_raw),
        'lang': get_user_lang(secret_raw, default=None),
    }
def cancel_pending(db, user_id, norad_id=None):
    query = update(Notification).where(Notification.user_id == user_id, Notification.state == 'pending')
    if norad_id is not None:
        query = query.where(Notification.norad_id == norad_id)
    db.execute(query.values(state='cancelled'))


@app.get('/healthz')
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
        db.execute(select(SystemSetting.key).limit(1))
    except SQLAlchemyError:
        raise HTTPException(503, 'database unavailable or migrations missing')
    return {'status': 'ok', 'version': '0.1.0'}


@app.get('/api/v1/auth/options')
def options(db: Session = Depends(get_db)):
    value = db.get(SystemSetting, 'registration_mode')
    return {'registration_mode': value.value if value else 'closed'}


@app.post('/api/v1/auth/login')
def login(data: Credentials, request: Request, response: Response, db: Session = Depends(get_db)):
    throttle(request, 'auth')
    user = db.scalar(select(User).where(User.username == data.username))
    if user is None or not verify_password(data.password, user.password_hash) or not user.approved:
        raise HTTPException(401, '用户名、密码错误或账号待审核')
    response.set_cookie('spa_session', serializer().dumps(user.id), httponly=True,
        secure=settings.cookie_secure, samesite='strict', max_age=86400)
    return profile(user)


@app.post('/api/v1/auth/logout')
def logout(response: Response):
    response.delete_cookie('spa_session')
    return {'ok': True}


@app.post('/api/v1/auth/register', status_code=201)
def register(data: Registration, request: Request, db: Session = Depends(get_db)):
    throttle(request, 'auth')
    mode = options(db)['registration_mode']
    if mode == 'closed':
        raise HTTPException(403, '注册已关闭')
    if mode == 'invite':
        digest = hashlib.sha256((data.invite or '').encode()).hexdigest()
        used = db.execute(update(Invite).where(Invite.token_hash == digest,
            Invite.used.is_(False), Invite.expires_at > utcnow()).values(used=True))
        if not used.rowcount:
            raise HTTPException(400, '邀请码无效或已使用')
    user = User(username=data.username, password_hash=hasher.hash(data.password),
        approved=(mode == 'invite'), is_admin=False)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '用户名不可用')
    return {'approved': user.approved}


@app.get('/api/v1/me')
def me(user: User = Depends(current_user)):
    return profile(user)


@app.put('/api/v1/me')
def update_me(data: Profile, user: User = Depends(current_user), db: Session = Depends(get_db)):
    for key, value in data.model_dump().items():
        setattr(user, key, value)
    # Delete only unsent predictions; retained sent records still deduplicate.
    for n in db.scalars(select(Notification).where(Notification.user_id == user.id,
            Notification.state.in_(['pending', 'cancelled']))):
        db.delete(n)
    db.commit()
    return profile(user)


@app.put('/api/v1/me/bark')
def bark_config(data: BarkConfig, user: User = Depends(current_user), db: Session = Depends(get_db)):
    existing = parse_channels(_decrypt_notify(user))
    try:
        key = normalize_bark_key(data.key)
    except PushError as exc:
        raise HTTPException(422, str(exc))
    if data.key:
        existing['bark'] = {'enabled': True, 'key': key}
    else:
        existing.pop('bark', None)
    user.bark_key = cipher().encrypt(json.dumps(existing).encode()).decode() if existing else None
    db.commit()
    return {'configured': has_configured_channel(_decrypt_notify(user))}


@app.put('/api/v1/me/notify')
async def notify_save(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(422, '无效的配置格式')
    existing = parse_channels(_decrypt_notify(user))
    # Validate before mutating or encrypting any provider configuration.
    try:
        for channel, field, normalizer in (
            ('bark', 'key', normalize_bark_key),
            ('serverchan', 'sendkey', normalize_serverchan_key),
        ):
            if channel not in body:
                continue
            cfg = body[channel]
            if not isinstance(cfg, dict):
                raise PushError('推送通道配置必须为对象')
            if not cfg.get('clear') and cfg.get(field) is not None and cfg.get(field) != '':
                cfg[field] = normalizer(cfg[field])
    except PushError as exc:
        raise HTTPException(422, str(exc))

    if 'bark' in body and isinstance(body['bark'], dict):
        b = body['bark']
        cur = existing.get('bark', {})
        new_key = normalize_bark_key(b.get('key', '')) if b.get('key') else cur.get('key', '')
        if b.get('clear'):
            existing.pop('bark', None)
        elif new_key:
            existing['bark'] = {'enabled': bool(b.get('enabled', True)), 'key': new_key}

    if 'serverchan' in body and isinstance(body['serverchan'], dict):
        b = body['serverchan']
        cur = existing.get('serverchan', {})
        val = (b.get('sendkey') or '').strip() or cur.get('sendkey', '')
        if b.get('clear'):
            existing.pop('serverchan', None)
        elif val:
            existing['serverchan'] = {'enabled': bool(b.get('enabled', True)), 'sendkey': val}

    if 'pushplus' in body and isinstance(body['pushplus'], dict):
        b = body['pushplus']
        cur = existing.get('pushplus', {})
        val = (b.get('token') or '').strip() or cur.get('token', '')
        if b.get('clear'):
            existing.pop('pushplus', None)
        elif val:
            existing['pushplus'] = {
                'enabled': bool(b.get('enabled', True)),
                'token': val,
                'topic': (b.get('topic') if b.get('topic') is not None else cur.get('topic', '')).strip(),
            }

    if 'telegram' in body and isinstance(body['telegram'], dict):
        b = body['telegram']
        cur = existing.get('telegram', {})
        tok = (b.get('bot_token') or '').strip() or cur.get('bot_token', '')
        cid = str(b.get('chat_id') if b.get('chat_id') is not None else cur.get('chat_id', '')).strip()
        if b.get('clear'):
            existing.pop('telegram', None)
        elif tok and cid:
            existing['telegram'] = {'enabled': bool(b.get('enabled', True)), 'bot_token': tok, 'chat_id': cid}

    if 'ntfy' in body and isinstance(body['ntfy'], dict):
        b = body['ntfy']
        cur = existing.get('ntfy', {})
        topic = (b.get('topic') if b.get('topic') is not None else cur.get('topic', '')).strip()
        srv = (b.get('server_url') or cur.get('server_url') or 'https://ntfy.sh').strip()
        tok = (b.get('token') or '').strip() or cur.get('token', '')
        if b.get('clear'):
            existing.pop('ntfy', None)
        elif topic:
            existing['ntfy'] = {'enabled': bool(b.get('enabled', True)), 'server_url': srv, 'topic': topic, 'token': tok}

    if 'bot_webhook' in body and isinstance(body['bot_webhook'], dict):
        b = body['bot_webhook']
        cur = existing.get('bot_webhook', {})
        url = (b.get('url') or '').strip() or cur.get('url', '')
        if b.get('clear'):
            existing.pop('bot_webhook', None)
        elif url:
            existing['bot_webhook'] = {'enabled': bool(b.get('enabled', True)), 'url': url}

    if 'webhook' in body and isinstance(body['webhook'], dict):
        b = body['webhook']
        cur = existing.get('webhook', {})
        url = (b.get('url') or '').strip() or cur.get('url', '')
        tok = (b.get('token') or '').strip() or cur.get('token', '')
        if b.get('clear'):
            existing.pop('webhook', None)
        elif url:
            existing['webhook'] = {'enabled': bool(b.get('enabled', True)), 'url': url, 'token': tok}

    if 'lang' in body:
        if body['lang'] not in SUPPORTED_LANGS:
            raise HTTPException(422, 'Unsupported notification language')
        existing['_lang'] = body['lang']
    user.bark_key = cipher().encrypt(json.dumps(existing).encode()).decode() if existing else None
    db.commit()
    return {'ok': True, 'lang': get_user_lang(_decrypt_notify(user), default=None),
            'notify_channels': mask_channels_summary(_decrypt_notify(user))}


@app.post('/api/v1/me/bark/test')
@app.post('/api/v1/me/notify/test')
async def bark_test(request: Request, user: User = Depends(current_user)):
    throttle(request, 'bark', 5)
    secret_raw = _decrypt_notify(user)
    if not has_configured_channel(secret_raw):
        raise HTTPException(400, '请先保存至少一个推送通道配置')
    only_channel = None
    try:
        data = await request.json()
        if isinstance(data, dict) and data.get('channel'):
            only_channel = str(data['channel'])
    except Exception:
        pass
    try:
        t_map = {'zh-CN': ('SPA \u6d4b\u8bd5\u901a\u77e5', '\u536b\u661f\u8fc7\u5883\u63d0\u9192\u901a\u9053\u5df2\u8fde\u63a5\u3002'), 'en': ('SPA Test Notification', 'Satellite pass alert channel is connected.'), 'ja': ('SPA \u30c6\u30b9\u30c8\u901a\u77e5', '\u885b\u661f\u30d1\u30b9\u901a\u77e5\u30c1\u30e3\u30f3\u30cd\u30eb\u304c\u63a5\u7d9a\u3055\u308c\u307e\u3057\u305f\u3002')}
        tt, tb = t_map.get(get_user_lang(secret_raw), t_map['zh-CN'])
        res = dispatch_notifications(secret_raw, tt, tb, only_channel=only_channel)
    except PushError as exc:
        raise HTTPException(502, str(exc))
    return {'ok': True, **res}


@app.get('/api/v1/satellites')
def satellites(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{k: getattr(s, k) for k in ('norad_id', 'name', 'epoch', 'fetched_at',
        'amsat_name', 'amsat_status', 'amsat_checked_at')} for s in db.scalars(select(Satellite).order_by(Satellite.name))]


@app.post('/api/v1/admin/satellites/import')
def import_orbit(data: OrbitInput, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    try:
        s = save_satellite(db, **data.model_dump())
        db.commit()
        return {'norad_id': s.norad_id, 'name': s.name}
    except (ValueError, KeyError, TypeError):
        db.rollback()
        raise HTTPException(422, '轨道格式或校验无效')


@app.post('/api/v1/admin/satellites/fetch')
def fetch_orbit(data: FetchInput, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    throttle(request, 'fetch', 20)
    try:
        s = refresh_one(db, **data.model_dump())
        db.commit()
        refresh_status_async()
        return {'norad_id': s.norad_id, 'name': s.name}
    except (ValueError, KeyError, TypeError, httpx.HTTPError):
        db.rollback()
        raise HTTPException(502, '卫星轨道获取失败；可切换数据源或手动导入 TLE/OMM')


@app.post('/api/v1/admin/satellites/sync-defaults')
def sync_defaults_route(data: SyncDefaultsInput, request: Request, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    throttle(request, 'sync_defaults', 10)
    count = sync_default_satellites(db, source=data.source, only_missing=False)
    db.commit()
    refresh_status_async()
    return {'synced': count}


@app.delete('/api/v1/admin/satellites/{norad_id}')
def delete_satellite(norad_id: int, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    sat = db.get(Satellite, norad_id)
    if sat is None:
        raise HTTPException(404, '未找到该卫星')
    db.execute(delete(Subscription).where(Subscription.norad_id == norad_id))
    db.execute(
        update(Notification)
        .where(Notification.norad_id == norad_id, Notification.state.in_(['pending', 'sending']))
        .values(state='cancelled', last_error='卫星已从目录删除')
    )
    db.delete(sat)
    db.commit()
    return {'ok': True, 'norad_id': norad_id}


@app.get('/api/v1/subscriptions')
def subscriptions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{k: getattr(s, k) for k in ('norad_id', 'min_elevation', 'lead_minutes', 'enabled')}
        for s in db.scalars(select(Subscription).where(Subscription.user_id == user.id))]


@app.put('/api/v1/subscriptions/{norad_id}')
def subscribe(norad_id: int, data: SubscriptionInput, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if db.get(Satellite, norad_id) is None:
        raise HTTPException(404, '未找到卫星')
    sub = db.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.norad_id == norad_id))
    if sub is None:
        sub = Subscription(user_id=user.id, norad_id=norad_id)
        db.add(sub)
    for key, value in data.model_dump().items():
        setattr(sub, key, value)
    for n in db.scalars(select(Notification).where(Notification.user_id == user.id,
            Notification.norad_id == norad_id, Notification.state.in_(['pending', 'cancelled']))):
        db.delete(n)
    db.commit()
    return {'ok': True}


@app.get('/api/v1/passes')
def passes(hours: int = Query(default=24, ge=1, le=72), user: User = Depends(current_user), db: Session = Depends(get_db)):
    result, warnings = [], []
    rows = db.execute(select(Subscription, Satellite).join(Satellite)
        .where(Subscription.user_id == user.id, Subscription.enabled.is_(True))).all()
    for sub, sat in rows:
        try:
            result.extend(predict(sat, user.latitude, user.longitude, user.altitude_m, utcnow(), hours, sub.min_elevation))
        except (ValueError, KeyError):
            warnings.append(f'{sat.name}: 轨道过期或无效，请管理员更新')
    return {'passes': sorted(result, key=lambda p: p['aos']), 'warnings': warnings}


@app.get('/api/v1/notifications')
def notifications(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{k: getattr(n, k) for k in ('id', 'norad_id', 'aos', 'due_at', 'title', 'state', 'attempts', 'last_error', 'sent_at')}
        for n in db.scalars(select(Notification).where(Notification.user_id == user.id)
            .order_by(Notification.id.desc()).limit(100))]


@app.get('/api/v1/admin/users')
def users(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    return [profile(u) for u in db.scalars(select(User).order_by(User.id))]


@app.put('/api/v1/admin/users/{user_id}/approval')
def approval(user_id: int, data: Approval, admin: User = Depends(admin_user), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, '用户不存在')
    if target.is_admin:
        raise HTTPException(400, '不能停用管理员')
    target.approved = data.approved
    if not data.approved:
        cancel_pending(db, user_id)
    db.commit()
    return {'ok': True}


@app.put('/api/v1/admin/registration')
def registration_mode(data: RegistrationMode, user: User = Depends(admin_user), db: Session = Depends(get_db)):
    db.merge(SystemSetting(key='registration_mode', value=data.mode))
    db.commit()
    return {'ok': True}


@app.post('/api/v1/admin/invites')
def invite(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    token = secrets.token_urlsafe(24)
    db.add(Invite(token_hash=hashlib.sha256(token.encode()).hexdigest(), expires_at=utcnow() + timedelta(days=7)))
    db.commit()
    return {'invite': token, 'expires_in_days': 7}


@app.get('/api/v1/admin/status')
def status(user: User = Depends(admin_user), db: Session = Depends(get_db)):
    last = db.get(SystemSetting, 'worker_last_tick')
    return {'worker_enabled': settings.worker_enabled, 'last_tick_utc': last.value if last else None}


if Path(settings.static_dir).is_dir():
    app.mount('/', StaticFiles(directory=settings.static_dir, html=True), name='frontend')
