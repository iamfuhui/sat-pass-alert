import json
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from skyfield.api import wgs84
from app.orbits import parse_elements, predict, ts

# Fixed archived OMM example from Skyfield's official earth-satellites documentation.
OMM = {'OBJECT_NAME':'ISS (ZARYA)','OBJECT_ID':'1998-067A','EPOCH':'2024-05-06T19:53:04.999776',
 'MEAN_MOTION':15.50957674,'ECCENTRICITY':0.000358,'INCLINATION':51.6393,
 'RA_OF_ASC_NODE':160.4574,'ARG_OF_PERICENTER':140.6673,'MEAN_ANOMALY':205.725,
 'EPHEMERIS_TYPE':0,'CLASSIFICATION_TYPE':'U','NORAD_CAT_ID':25544,
 'ELEMENT_SET_NO':999,'REV_AT_EPOCH':45212,'BSTAR':0.0002731,'MEAN_MOTION_DOT':0.00015698,'MEAN_MOTION_DDOT':0}
START=datetime(2024,5,7,tzinfo=timezone.utc)


def satellite():
    return SimpleNamespace(norad_id=25544,name='ISS',elements=json.dumps(OMM),format='omm',amsat_status='Not Heard')


def test_pass_geometry_and_filter():
    sat=satellite()
    passes=predict(sat,30.6,104.1,500,START,24,0)
    assert len(passes)>0
    orbit=parse_elements(sat.elements,'omm')
    station=wgs84.latlon(30.6,104.1,elevation_m=500)
    for p in passes:
        aos,peak,los=[datetime.fromisoformat(p[k]) for k in ('aos','peak_at','los')]
        assert aos<peak<los
        assert 0 <= p['aos_azimuth'] <= 360
        assert 0 < p['max_elevation'] <= 90
        for at in (aos,los):
            assert abs((orbit-station).at(ts.from_datetime(at)).altaz()[0].degrees)<0.02
        assert p['amsat_status']=='Not Heard'  # Status is informational, never a filter.
    high=predict(sat,30.6,104.1,500,START,24,30)
    assert high == [p for p in passes if p['max_elevation'] >= 30]


def test_stale_elements_rejected():
    with pytest.raises(ValueError,match='过期'):
        predict(satellite(),30,104,0,datetime(2026,1,1,tzinfo=timezone.utc))


def test_tle_validation():
    with pytest.raises(ValueError):
        parse_elements('invalid\ninvalid')
    with pytest.raises(ValueError):
        parse_elements('1 '+'0'*67+'\n2 '+'0'*67)
