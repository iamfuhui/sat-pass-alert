import json
import math
from datetime import timedelta, timezone
from skyfield.api import EarthSatellite, load, wgs84
from .config import settings

ts = load.timescale(builtin=True)


def parse_elements(elements, format='tle', name=''):
    if format == 'omm':
        sat = EarthSatellite.from_omm(ts, json.loads(elements))
    else:
        lines = [x.strip() for x in elements.splitlines() if x.strip()]
        if len(lines) == 3:
            name, *lines = lines
        if len(lines) != 2 or not lines[0].startswith('1 ') or not lines[1].startswith('2 '):
            raise ValueError('需要两行 TLE 或名称加两行 TLE')
        if lines[0][2:7] != lines[1][2:7] or any(len(line) < 69 for line in lines):
            raise ValueError('TLE 长度或卫星编号不匹配')
        for line in lines:
            checksum = sum(int(c) if c.isdigit() else 1 if c == '-' else 0 for c in line[:68]) % 10
            if not line[68].isdigit() or checksum != int(line[68]):
                raise ValueError('TLE 校验和无效')
        sat = EarthSatellite(*lines, name, ts)
    if not math.isfinite(sat.model.no_kozai) or sat.model.no_kozai <= 0 or not 0 <= sat.model.ecco < 1:
        raise ValueError('轨道参数无效')
    return sat


def predict(satellite, latitude, longitude, altitude_m, start, hours=24, min_elevation=10):
    sat = parse_elements(satellite.elements, satellite.format, satellite.name)
    start = start.replace(tzinfo=timezone.utc) if start.tzinfo is None else start.astimezone(timezone.utc)
    end = start + timedelta(hours=hours)
    if max(abs((t - sat.epoch.utc_datetime()).total_seconds()) for t in (start, end)) > settings.tle_max_age_days * 86400:
        raise ValueError('轨道数据已过期，请刷新后重试')
    observer = wgs84.latlon(latitude, longitude, elevation_m=altitude_m)
    times, events = sat.find_events(observer, ts.from_datetime(start - timedelta(hours=2)),
        ts.from_datetime(end + timedelta(hours=2)), altitude_degrees=0)
    results, rising, peaks = [], None, []
    for t, event in zip(times, events):
        at = t.utc_datetime()
        elevation, azimuth, _ = (sat - observer).at(t).altaz()
        if event == 0:
            rising, peaks = (at, azimuth.degrees), []
        elif event == 1 and rising:
            peaks.append((elevation.degrees, at))
        elif event == 2 and rising:
            if peaks:
                peak, peak_at = max(peaks)
                if start <= rising[0] < end and math.isfinite(peak) and peak >= min_elevation:
                    _, peak_az, _ = (sat - observer).at(ts.from_datetime(peak_at)).altaz()
                    span = (at - rising[0]).total_seconds()
                    track = []
                    for i in range(17):
                        st = rising[0] + timedelta(seconds=span * i / 16)
                        sel, saz, _ = (sat - observer).at(ts.from_datetime(st)).altaz()
                        track.append([round(saz.degrees, 1), round(max(0.0, sel.degrees), 1)])
                    results.append({'norad_id': satellite.norad_id, 'name': satellite.name,
                        'aos': rising[0].isoformat(), 'los': at.isoformat(), 'peak_at': peak_at.isoformat(),
                        'max_elevation': round(peak, 2), 'aos_azimuth': round(rising[1], 1),
                        'peak_azimuth': round(peak_az.degrees, 1),
                        'los_azimuth': round(azimuth.degrees, 1),
                        'track': track,
                        'amsat_status': satellite.amsat_status or '未知（不影响提醒）'})
            rising, peaks = None, []
    return results
