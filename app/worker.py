import re
import logging
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from sqlalchemy import select, update, delete
from sqlalchemy.exc import IntegrityError
from cryptography.fernet import InvalidToken
from .db import SessionLocal, utcnow
from .models import User, Satellite, Subscription, Notification, SystemSetting
from .orbits import predict
from .catalog import refresh_one
from .amsat import refresh_status_async
from .bark import dispatch_notifications, has_configured_channel, parse_channels, PushError
from .security import cipher

log = logging.getLogger(__name__)
tick_lock = threading.Lock()



SUPPORTED_LANGS = ('zh-CN', 'en', 'ja')

def get_user_lang(secret_raw, default='zh-CN'):
    lang = parse_channels(secret_raw).get('_lang')
    return lang if lang in SUPPORTED_LANGS else default


def _localize_amsat(status, lang):
    if not status or lang == 'zh-CN':
        return status
    m_min = re.search(r'\uff08(\d+)\u5206\u949f\u524d\u62a5\u544a\uff09', status)
    m_hr = re.search(r'\uff08(\d+)\u5c0f\u65f6\u524d\u62a5\u544a\uff09', status)
    m_day = re.search(r'\uff08(\d+)\u5929\u524d\u62a5\u544a\uff09', status)
    base = status.split('\uff08')[0]
    if lang == 'en':
        if base.startswith('\u672a\u77e5'): return 'Unknown (no recent AMSAT report)'
        if '刚刚报告' in status: return f'{base} (just reported)'
        if m_min: return f"{base} ({m_min.group(1)}m ago)"
        if m_hr: return f"{base} ({m_hr.group(1)}h ago)"
        if m_day: return f"{base} ({m_day.group(1)}d ago)"
        return base
    if lang == 'ja':
        if base.startswith('\u672a\u77e5'): return '\u4e0d\u660e\uff08\u76f4\u8fd1\u306eAMSAT\u5831\u544a\u306a\u3057\uff09'
        if '刚刚报告' in status: return f'{base}（報告直後）'
        if m_min: return f"{base}\uff08{m_min.group(1)}\u5206\u524d\u306b\u5831\u544a\uff09"
        if m_hr: return f"{base}\uff08{m_hr.group(1)}\u6642\u9593\u524d\u306b\u5831\u544a\uff09"
        if m_day: return f"{base}\uff08{m_day.group(1)}\u65e5\u524d\u306b\u5831\u544a\uff09"
        return base
    return status

def format_pass_message(sat_name, p, tz_name, lang):
    tz = ZoneInfo(tz_name)
    la = datetime.fromisoformat(p['aos']).astimezone(tz)
    ll = datetime.fromisoformat(p['los']).astimezone(tz)
    am = _localize_amsat(p.get('amsat_status', ''), lang)
    if lang == 'en':
        return (f"SPA \u00b7 {sat_name} Upcoming Pass",
                f"AOS {la:%Y-%m-%d %H:%M:%S %Z} / Az {p['aos_azimuth']}\u00b0\nMax El {p['max_elevation']}\u00b0 / LOS {ll:%H:%M:%S}\nAMSAT: {am} (ref only)")
    if lang == 'ja':
        return (f"SPA \u00b7 {sat_name} \u30d1\u30b9\u63a5\u8fd1",
                f"AOS {la:%Y-%m-%d %H:%M:%S %Z} / \u65b9\u4f4d\u89d2 {p['aos_azimuth']}\u00b0\n\u6700\u5927\u4ef0\u89d2 {p['max_elevation']}\u00b0 / LOS {ll:%H:%M:%S}\nAMSAT: {am}\uff08\u53c2\u8003\u60c5\u5831\uff09")
    return (f"SPA \u00b7 {sat_name} \u5373\u5c06\u8fc7\u5883",
            f"AOS {la:%Y-%m-%d %H:%M:%S %Z} / \u65b9\u4f4d {p['aos_azimuth']}\u00b0\n\u6700\u9ad8\u4ef0\u89d2 {p['max_elevation']}\u00b0 / LOS {ll:%H:%M:%S}\nAMSAT: {am}\uff08\u4ec5\u4f9b\u53c2\u8003\uff09")

def plan(db, now):
    rows = db.execute(select(Subscription, User, Satellite)
        .join(User, User.id == Subscription.user_id)
        .join(Satellite, Satellite.norad_id == Subscription.norad_id)
        .where(Subscription.enabled.is_(True), User.approved.is_(True))).all()
    for sub, user, sat in rows:
        if not user.bark_key:
            continue
        try:
            secret = cipher().decrypt(user.bark_key.encode()).decode()
        except InvalidToken:
            continue
        if not has_configured_channel(secret):
            continue
        lang = get_user_lang(secret)
        try:
            passes = predict(sat, user.latitude, user.longitude, user.altitude_m, now, 24, sub.min_elevation)
        except (ValueError, KeyError):
            db.execute(update(Notification).where(Notification.user_id == user.id, Notification.norad_id == sat.norad_id, Notification.state == 'pending').values(state='cancelled', last_error='轨道数据过期或无效'))
            continue
        predicted_aos = [datetime.fromisoformat(p['aos']).replace(tzinfo=None) for p in passes]
        for pending in db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.norad_id == sat.norad_id, Notification.state == 'pending', Notification.aos > now)):
            if not any(abs((pending.aos - at).total_seconds()) < 600 for at in predicted_aos):
                pending.state = 'cancelled'
        for p in passes:
            aos = datetime.fromisoformat(p['aos']).replace(tzinfo=None, microsecond=0)
            # TLE refresh/numerical search can move AOS; reconcile within the same LEO pass.
            title, body = format_pass_message(sat.name, p, user.timezone, lang)
            existing = db.scalar(select(Notification).where(
                Notification.user_id == user.id, Notification.norad_id == sat.norad_id,
                Notification.aos.between(aos - timedelta(minutes=10), aos + timedelta(minutes=10))))
            if existing:
                if existing.state == 'cancelled' and existing.attempts == 0:
                    existing.state = 'pending'
                    existing.last_error = None
                if existing.state == 'pending':
                    if existing.attempts == 0:
                        existing.due_at = aos - timedelta(minutes=sub.lead_minutes)
                    existing.title = title
                    existing.body = body
                    existing.aos = aos
                continue
            n = Notification(user_id=user.id, norad_id=sat.norad_id, aos=aos,
                due_at=aos - timedelta(minutes=sub.lead_minutes), title=title, body=body)
            try:
                with db.begin_nested():
                    db.add(n)
                    db.flush()
            except IntegrityError:
                pass
    db.commit()


def deliver(db, now, sender=dispatch_notifications, *, clock=utcnow):
    db.execute(update(Notification).where(Notification.state == 'pending', Notification.aos <= now).values(state='expired'))
    db.commit()
    rows = db.scalars(select(Notification).where(Notification.state == 'pending',
        Notification.due_at <= now).order_by(Notification.due_at).limit(100)).all()
    for n in rows:
        user = db.get(User, n.user_id)
        sub = db.scalar(select(Subscription).where(Subscription.user_id == n.user_id,
            Subscription.norad_id == n.norad_id, Subscription.enabled.is_(True)))
        cur_now = clock()
        if n.aos <= cur_now or not user or not user.approved or not user.bark_key or not sub:
            n.state = 'expired' if n.aos <= cur_now else 'cancelled'
            db.commit()
            continue
        claimed = db.execute(update(Notification).where(Notification.id == n.id,
            Notification.state == 'pending', Notification.aos > cur_now, Notification.due_at <= cur_now)
            .values(state='sending', attempts=Notification.attempts + 1))
        db.commit()
        if not claimed.rowcount:
            continue
        try:
            secret = cipher().decrypt(user.bark_key.encode()).decode()
            if n.aos <= clock():
                n.state = 'expired'
                n.attempts -= 1
            elif not has_configured_channel(secret):
                n.state = 'cancelled'
                n.attempts -= 1
            else:
                sender(secret, n.title, n.body)
                n.state, n.sent_at, n.last_error = 'sent', clock(), None
        except (PushError, InvalidToken):
            db.refresh(n)
            n.last_error = '推送失败；检查 Bark 配置或网络'
            n.state = 'failed' if n.attempts >= 3 else 'pending'
            n.due_at = clock() + timedelta(seconds=60 * 2 ** n.attempts)
        db.commit()


def run_tick():
    if not tick_lock.acquire(blocking=False):
        return
    try:
        with SessionLocal() as db:
            now = utcnow()
            # Interrupted sends are uncertain: never blindly retry and duplicate a delivered push.
            db.execute(update(Notification).where(Notification.state == 'sending')
                .values(state='unknown', last_error='进程中断，发送结果未知'))
            plan(db, now)
            deliver(db, utcnow())
            db.merge(SystemSetting(key='worker_last_tick', value=now.isoformat()))
            db.execute(delete(Notification).where(Notification.aos < now - timedelta(days=30)))
            db.commit()
    finally:
        tick_lock.release()


_orbit_lock = threading.Lock()


def _refresh_stale_orbits_async(stop, cutoff):
    if not _orbit_lock.acquire(blocking=False):
        return

    def _run():
        try:
            with SessionLocal() as db:
                ids = db.scalars(select(Satellite.norad_id).where(
                    Satellite.fetched_at < cutoff)).all()
                for norad_id in ids:
                    if stop.is_set():
                        break
                    try:
                        refresh_one(db, norad_id, source='auto')
                        db.commit()
                    except Exception:
                        db.rollback()
                        log.warning('Orbit refresh failed for NORAD %s', norad_id)
        finally:
            _orbit_lock.release()

    threading.Thread(target=_run, name='orbit-refresh', daemon=True).start()


def loop(stop):
    last_refresh = None
    last_amsat = None
    while not stop.is_set():
        try:
            now = utcnow()
            if last_amsat is None or now - last_amsat > timedelta(hours=1):
                refresh_status_async()
                last_amsat = now
            run_tick()
            if last_refresh is None or now - last_refresh > timedelta(hours=6):
                _refresh_stale_orbits_async(stop, now - timedelta(hours=6))
                last_refresh = now
        except Exception:
            log.exception('Worker tick failed')
        stop.wait(30)
