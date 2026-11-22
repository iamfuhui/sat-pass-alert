"""Read-only, best-effort adapter for the public AMSAT status chart."""
import re
import threading
from datetime import datetime, timedelta
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from .models import Satellite
from .db import SessionLocal, utcnow

URL = 'https://status.amsat.org/'
COLORS = {
    '648fff': 'Sat/Mode Active',
    'ffb000': 'Telemetry/Beacon Only',
    'dc267f': 'Not Heard',
    'fe6100': 'Conflicting Reports',
}
REPORT_MAX_AGE = timedelta(hours=24)
_bg_lock = threading.Lock()


def _format_report_age(delta: timedelta) -> str:
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 1:
        return '刚刚报告'
    if mins < 60:
        return f'{mins}分钟前报告'
    return f'{mins // 60}小时前报告'


def parse_status(html, name, now):
    soup = BeautifulSoup(html, 'html.parser')
    link = next((a for a in soup.find_all('a') if a.get_text(strip=True) == name), None)
    if not link:
        return '未知（未匹配 AMSAT 名称）'
    row = link.find_parent('tr')
    if row is None:
        return '未知'
    for cell in row.find_all('td')[1:]:
        color = cell.get('bgcolor', '').lower().lstrip('#')
        if color not in COLORS:
            continue
        trigger = cell.find('a', onmouseover=True)
        if not trigger:
            continue
        match = re.search(r"docTips.show\('([a-z0-9]+)'\)", trigger['onmouseover'])
        if not match:
            continue
        tip = re.search(r'tips\.' + re.escape(match[1]) + r"\s*=.*?;(?:\r?\n|$)", html)
        dates = re.findall(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC', tip.group(0) if tip else '')
        if not dates:
            return '未知（报告时间无法解析）'
        latest = max(datetime.strptime(d, '%Y-%m-%d %H:%M UTC') for d in dates)
        age = now - latest
        if age > REPORT_MAX_AGE or latest > now + timedelta(minutes=5):
            return '未知（24 小时内无报告）'
        return f'{COLORS[color]}（{_format_report_age(age)}）'
    return '未知（近期无报告）'


def refresh_status(db):
    sats = db.scalars(select(Satellite).where(Satellite.amsat_name.is_not(None))).all()
    if not sats:
        return
    now = utcnow()
    try:
        with httpx.Client(timeout=12, follow_redirects=False) as client:
            response = client.get(URL)
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise ValueError('response too large')
        for sat in sats:
            sat.amsat_status = parse_status(response.text, sat.amsat_name, now)
            sat.amsat_checked_at = now
    except (httpx.HTTPError, ValueError):
        for sat in sats:
            if not sat.amsat_status:
                sat.amsat_status = '未知（AMSAT 暂不可用）'
            sat.amsat_checked_at = now
    db.commit()


def refresh_status_async():
    if not _bg_lock.acquire(blocking=False):
        return

    def _run():
        try:
            with SessionLocal() as db:
                refresh_status(db)
        finally:
            _bg_lock.release()

    threading.Thread(target=_run, name='amsat-refresh', daemon=True).start()
