"""Read-only, best-effort adapter for the public AMSAT status chart."""
import re
from datetime import datetime, timedelta
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from .models import Satellite
from .db import utcnow

URL = 'https://status.amsat.org/'
COLORS = {'648fff': 'Sat/Mode Active', 'ffb000': 'TLM/Beacon only',
          'dc267f': 'Not Heard', 'fe6100': 'Conflicting reports'}


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
            return '未知（报告时间不可解析）'
        latest = max(datetime.strptime(d, '%Y-%m-%d %H:%M UTC') for d in dates)
        if now - latest > timedelta(hours=24) or latest > now + timedelta(minutes=5):
            return '未知（无最近 24 小时报告）'
        return f'{COLORS[color]} · {latest:%m-%d %H:%M} UTC'
    return '未知（无最近报告）'


def refresh_status(db):
    sats = db.scalars(select(Satellite).where(Satellite.amsat_name.is_not(None))).all()
    if not sats:
        return
    now = utcnow()
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get(URL)
            response.raise_for_status()
            if len(response.content) > 5_000_000:
                raise ValueError('response too large')
        for sat in sats:
            sat.amsat_status = parse_status(response.text, sat.amsat_name, now)
            sat.amsat_checked_at = now
    except (httpx.HTTPError, ValueError):
        for sat in sats:
            sat.amsat_status = '未知（AMSAT 暂不可用）'
            sat.amsat_checked_at = now
    db.commit()
