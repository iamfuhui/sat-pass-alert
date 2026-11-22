import json
import re
import time
from datetime import datetime, timezone
import httpx
from sqlalchemy import select
from .db import utcnow
from .models import Satellite
from .orbits import parse_elements

CELESTRAK = 'https://celestrak.org/NORAD/elements/gp.php'
AMSAT_TLE_URLS = (
    'https://www.amsat.org/tle/current/nasabare.txt',
    'https://amsat.org/tle/current/nasabare.txt',
)

DEFAULT_SATELLITES = [
    {'norad_id': 25544, 'name': 'ARISS', 'amsat_name': 'ISS_[FM]', 'aliases': ('ISS', 'ZARYA', 'ARISS')},
    {'norad_id': 27607, 'name': 'SO-50 (SaudiSat-1C)', 'amsat_name': 'SO-50_[FM]', 'aliases': ('SO-50', 'SAUDISAT 1C', 'SAUDISAT-1C')},
    {'norad_id': 43678, 'name': 'PO-101 (DIWATA-2)', 'amsat_name': 'PO-101_[FM]', 'aliases': ('PO-101', 'DIWATA-2', 'DIWATA 2')},
    {'norad_id': 61781, 'name': 'AO-123 (ASRTU-1)', 'amsat_name': 'AO-123_[FM]', 'aliases': ('AO-123', 'ASRTU-1', 'ASRTU 1')},
    {'norad_id': 40931, 'name': 'IO-86 (LAPAN-A2)', 'amsat_name': 'IO-86_[FM]', 'aliases': ('IO-86', 'LAPAN-A2', 'LAPAN A2')},
    {'norad_id': 44909, 'name': 'RS-44 (BREEZE-KM R/B)', 'amsat_name': 'RS-44_[V/u]', 'aliases': ('RS-44', 'DOSAAF-85', 'BREEZE-KM')},
    {'norad_id': 7530, 'name': 'AO-7', 'amsat_name': 'AO-7_[U/v]', 'aliases': ('AO-7', 'AO-07', 'OSCAR 7')},
    {'norad_id': 39444, 'name': 'AO-73 (FUNCUBE-1)', 'amsat_name': 'AO-73_[U/v]', 'aliases': ('AO-73', 'FUNCUBE-1', 'FUNCUBE 1')},
    {'norad_id': 43803, 'name': 'JO-97 (JY1SAT)', 'amsat_name': 'JO-97_[U/v]', 'aliases': ('JO-97', 'JY1SAT')},
    {'norad_id': 24278, 'name': 'FO-29 (JAS-2)', 'amsat_name': 'FO-29_[V/u]', 'aliases': ('FO-29', 'JAS-2', 'JAS 2')},
    {'norad_id': 101101, 'name': 'SO-131 (UNNE-1B)', 'amsat_name': 'SO-131_[V/u_FM]', 'aliases': ('SO-131', 'SO131', 'UNNE-1B')},
]

_amsat_cache = {'fetched_at': 0.0, 'entries': []}


def _norm_name(text: str) -> str:
    return re.sub(r'[^A-Z0-9]+', '', (text or '').upper())


def _parse_amsat_tle_text(raw: str):
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    entries = []
    i = 0
    while i < len(lines) - 1:
        if lines[i].startswith('1 ') and lines[i + 1].startswith('2 '):
            l1, l2 = lines[i], lines[i + 1]
            title = lines[i - 1] if i > 0 and not lines[i - 1].startswith(('1 ', '2 ')) else ''
            try:
                nid = int(l1[2:7])
                entries.append({
                    'norad_id': nid,
                    'title': title,
                    'norm': _norm_name(title),
                    'tle': f'{title}\n{l1}\n{l2}' if title else f'{l1}\n{l2}',
                })
            except ValueError:
                pass
            i += 2
        else:
            i += 1
    return entries


def fetch_amsat_catalog(force: bool = False):
    now = time.monotonic()
    if not force and _amsat_cache['entries'] and now - _amsat_cache['fetched_at'] < 600:
        return _amsat_cache['entries']
    last_err = None
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for url in AMSAT_TLE_URLS:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                if len(resp.content) > 2_000_000:
                    raise ValueError('AMSAT 轨道响应过大')
                entries = _parse_amsat_tle_text(resp.text)
                if entries:
                    _amsat_cache['entries'] = entries
                    _amsat_cache['fetched_at'] = now
                    return entries
            except (httpx.HTTPError, ValueError) as exc:
                last_err = exc
    if _amsat_cache['entries']:
        return _amsat_cache['entries']
    raise ValueError('AMSAT 星历库获取失败') from last_err


def save_satellite(db, elements, format, name='', amsat_name=None, expected_id=None):
    orbit = parse_elements(elements, format, name)
    norad_id = orbit.model.satnum
    if expected_id is not None and norad_id != expected_id:
        raise ValueError('轨道编号与请求不一致')
    sat = db.get(Satellite, norad_id)
    if sat is None:
        sat = Satellite(norad_id=norad_id)
        db.add(sat)
    sat.name = (name or orbit.name or str(norad_id))[:128]
    sat.elements, sat.format = elements, format
    sat.epoch = orbit.epoch.utc_datetime().replace(tzinfo=None)
    sat.fetched_at = utcnow()
    if amsat_name is not None:
        sat.amsat_name = amsat_name
    db.flush()
    return sat


def _refresh_from_amsat(db, norad_id=None, amsat_name=None, name=None, aliases=()):
    entries = fetch_amsat_catalog()
    match = None
    if norad_id is not None:
        match = next((e for e in entries if e['norad_id'] == norad_id), None)
    if match is None:
        targets = {_norm_name(x) for x in (name, amsat_name, *aliases) if x}
        match = next((e for e in entries if e['norm'] in targets or any(t and t in e['norm'] for t in targets)), None)
    if match is None:
        raise ValueError('AMSAT 星历库中未找到该卫星')
    display_name = name or match['title'] or str(match['norad_id'])
    return save_satellite(db, match['tle'], 'tle', name=display_name, amsat_name=amsat_name, expected_id=norad_id)


def _refresh_from_celestrak(db, norad_id=None, amsat_name=None, name=None):
    params = {'CATNR': norad_id, 'FORMAT': 'JSON'} if norad_id is not None else {'NAME': name, 'FORMAT': 'JSON'}
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(CELESTRAK, params=params)
        response.raise_for_status()
        if len(response.content) > 100_000:
            raise ValueError('轨道响应过大')
        data = response.json()
    if not isinstance(data, list) or not data:
        raise ValueError('CelesTrak 未找到卫星轨道')
    item = data[0] if norad_id is not None else next(
        (x for x in data if _norm_name(x.get('OBJECT_NAME', '')) == _norm_name(name)),
        data[0],
    )
    return save_satellite(
        db,
        json.dumps(item),
        'omm',
        name=name or item.get('OBJECT_NAME', ''),
        amsat_name=amsat_name,
        expected_id=norad_id,
    )


def refresh_one(db, norad_id=None, amsat_name=None, source='celestrak', name=None, aliases=()):
    if source == 'amsat':
        return _refresh_from_amsat(db, norad_id=norad_id, amsat_name=amsat_name, name=name, aliases=aliases)
    if source == 'celestrak':
        return _refresh_from_celestrak(db, norad_id=norad_id, amsat_name=amsat_name, name=name)
    # auto: 优先 AMSAT 星历库，失败或未收录时回退 CelesTrak
    try:
        return _refresh_from_amsat(db, norad_id=norad_id, amsat_name=amsat_name, name=name, aliases=aliases)
    except Exception:
        return _refresh_from_celestrak(db, norad_id=norad_id, amsat_name=amsat_name, name=name)


def sync_default_satellites(db, source='auto', only_missing=False):
    existing_ids = set(db.scalars(select(Satellite.norad_id)).all())
    existing_names = {_norm_name(n) for n in db.scalars(select(Satellite.name)).all()}
    synced = 0
    for item in DEFAULT_SATELLITES:
        nid = item['norad_id']
        if only_missing:
            if nid is not None and nid in existing_ids:
                continue
            if _norm_name(item['name']) in existing_names:
                continue
        try:
            refresh_one(
                db,
                norad_id=nid,
                amsat_name=item['amsat_name'],
                source=source,
                name=item['name'],
                aliases=item.get('aliases', ()),
            )
            synced += 1
        except Exception:
            continue
    return synced
