import pytest
from app.locator import grid_center


@pytest.mark.parametrize('grid,lat,lon',[
 ('om20',30.5,105), (' OM20bo ',30.60416667,104.125),
 ('OM20bo24',30.60208333,104.10416667),('AA00',-89.5,-179),('RR99',89.5,179),
 ('JJ00AA00',0.00208333,0.00416667)])
def test_grid_centers(grid,lat,lon):
    normalized,actual_lat,actual_lon=grid_center(grid)
    assert normalized==grid.strip().upper()
    assert actual_lat==pytest.approx(lat,abs=1e-8)
    assert actual_lon==pytest.approx(lon,abs=1e-8)


@pytest.mark.parametrize('grid',['','OM','OM2','OM20B','OM20BO2','OM20BO240','SS00','OM20YY','OM2X','OM 20','１２３４'])
def test_invalid_grids(grid):
    with pytest.raises(ValueError):
        grid_center(grid)


def test_api_converts_and_persists_locator(admin):
    r=admin.put('/api/v1/me',json={'locator':'om20bo24','altitude_m':500,'timezone':'Asia/Shanghai'})
    assert r.status_code==200
    data=r.json()
    assert data['latitude']==pytest.approx(30.60208333,abs=1e-6)
    assert data['longitude']==pytest.approx(104.10416667,abs=1e-6)
    assert admin.get('/api/v1/me').json()['locator']=='OM20BO24'
    assert admin.put('/api/v1/me',json={'locator':'OM20B'}).status_code==422
    assert admin.put('/api/v1/me',json={'latitude':30.6,'longitude':104.1}).json()['locator'] is None
