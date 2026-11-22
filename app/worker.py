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
from .bark import dispatch_notifications, PushError
from .security import cipher

log = logging.getLogger(__name__)
tick_lock = threading.Lock()


def plan(db, now):
    rows = db.execute(select(Subscription, User, Satellite)
        .join(User, User.id == Subscription.user_id)
        .join(Satellite, Satellite.norad_id == Subscription.norad_id)
        .where(Subscription.enabled.is_(True), User.approved.is_(True))).all()
    for sub, user, sat in rows:
        if not user.bark_key:
            continue
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
            local = datetime.fromisoformat(p['aos']).astimezone(ZoneInfo(user.timezone))
            body = (f"AOS {local:%Y-%m-%d %H:%M:%S %Z} / 方位 {p['aos_azimuth']}°\n"
                    f"最高仰角 {p['max_elevation']}° / LOS {datetime.fromisoformat(p['los']).astimezone(ZoneInfo(user.timezone)):%H:%M:%S}\n"
                    f"AMSAT: {p['amsat_status']}（仅供参考）")
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
                    existing.body = body
                    existing.aos = aos
                continue
            n = Notification(user_id=user.id, norad_id=sat.norad_id, aos=aos,
                due_at=aos - timedelta(minutes=sub.lead_minutes), title=f'SPA · {sat.name} 即将过境', body=body)
            try:
                with db.begin_nested():
                    db.add(n)
                    db.flush()
            except IntegrityError:
                pass
    db.commit()


def deliver(db, now, sender=dispatch_notifications):
    db.execute(update(Notification).where(Notification.state == 'pending', Notification.aos <= now).values(state='expired'))
    db.commit()
    rows = db.scalars(select(Notification).where(Notification.state == 'pending',
        Notification.due_at <= now).order_by(Notification.due_at).limit(100)).all()
    for n in rows:
        cur_now = utcnow()
        user = db.get(User, n.user_id)
        sub = db.scalar(select(Subscription).where(Subscription.user_id == n.user_id,
            Subscription.norad_id == n.norad_id, Subscription.enabled.is_(True)))
        if n.aos <= cur_now or not user or not user.approved or not user.bark_key or not sub:
            n.state = 'expired' if n.aos <= cur_now else 'cancelled'
            db.commit()
            continue
        claimed = db.execute(update(Notification).where(Notification.id == n.id,
            Notification.state == 'pending').values(state='sending', attempts=Notification.attempts + 1))
        db.commit()
        if not claimed.rowcount:
            continue
        try:
            sender(cipher().decrypt(user.bark_key.encode()).decode(), n.title, n.body)
            n.state, n.sent_at, n.last_error = 'sent', utcnow(), None
        except (PushError, InvalidToken):
            db.refresh(n)
            n.last_error = '推送失败；检查 Bark 配置或网络'
            n.state = 'failed' if n.attempts >= 3 else 'pending'
            n.due_at = utcnow() + timedelta(seconds=60 * 2 ** n.attempts)
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
            deliver(db, now)
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
