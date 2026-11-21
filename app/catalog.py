import json
import httpx
from sqlalchemy import select
from .db import utcnow
from .models import Satellite
from .orbits import parse_elements

CELESTRAK = 'https://celestrak.org/NORAD/elements/gp.php'


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


def refresh_one(db, norad_id, amsat_name=None):
    with httpx.Client(timeout=20, follow_redirects=False) as client:
        response = client.get(CELESTRAK, params={'CATNR': norad_id, 'FORMAT': 'JSON'})
        response.raise_for_status()
        if len(response.content) > 100_000:
            raise ValueError('轨道响应过大')
        data = response.json()
    if not isinstance(data, list) or len(data) != 1:
        raise ValueError('未找到卫星轨道')
    return save_satellite(db, json.dumps(data[0]), 'omm', amsat_name=amsat_name, expected_id=norad_id)
