import json
from unittest.mock import patch
import pytest

from elevation_cli import get_annual_temperature, get_annual_precipitation
from elevation_cli import get_soil_type

SAMPLE_DAILY_RESPONSE = {
    "daily": {
        # one entry per month so aggregation to monthly means works in tests
        "time": [
            "1991-01-01", "1991-02-01", "1991-03-01", "1991-04-01",
            "1991-05-01", "1991-06-01", "1991-07-01", "1991-08-01",
            "1991-09-01", "1991-10-01", "1991-11-01", "1991-12-01",
        ],
        "temperature_2m_mean": [5.0, 7.0, 12.0, 15.0, 20.0, 24.0, 25.0, 24.0, 19.0, 14.0, 9.0, 6.0],
        "precipitation_sum": [5.0, 10.0, 20.0, 30.0, 40.0, 50.0, 150.0, 120.0, 60.0, 30.0, 15.0, 10.0]
    }
}

class DummyResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception("HTTP Error")
    def json(self):
        return self._json

@patch('elevation_cli.requests.get')
def test_get_annual_temperature(mock_get):
    mock_get.return_value = DummyResponse(SAMPLE_DAILY_RESPONSE)
    res = get_annual_temperature(0, 0, timeout=1)
    assert res['temp_provider'] == 'open-meteo-climate'
    assert isinstance(res['temp_min_c'], float)
    assert isinstance(res['temp_max_c'], float)
    assert isinstance(res['temp_mean_c'], float)

@patch('elevation_cli.requests.get')
def test_get_annual_precipitation(mock_get):
    mock_get.return_value = DummyResponse(SAMPLE_DAILY_RESPONSE)
    res = get_annual_precipitation(0, 0, timeout=1)
    assert res['precip_provider'] == 'open-meteo-climate'
    assert isinstance(res['annual_precip_mm'], float)
    assert 1 <= res['wettest_month'] <= 12
    assert 1 <= res['driest_month'] <= 12


@patch('elevation_cli.requests.get')
def test_incomplete_months_return_nulls(mock_get):
    # Response missing many months -> functions should return null provider and None fields
    incomplete = {"daily": {"time": ["1991-01-01"], "temperature_2m_mean": [10], "precipitation_sum": [0]}}
    mock_get.return_value = DummyResponse(incomplete)
    t = get_annual_temperature(0, 0, timeout=1)
    p = get_annual_precipitation(0, 0, timeout=1)
    assert t['temp_provider'] is None
    assert p['precip_provider'] is None


@patch('elevation_cli.requests.get')
def test_http_error_returns_nulls(mock_get):
    mock_get.return_value = DummyResponse({"error": True}, status_code=500)
    t = get_annual_temperature(0, 0, timeout=1)
    p = get_annual_precipitation(0, 0, timeout=1)
    assert t['temp_provider'] is None
    assert p['precip_provider'] is None


@patch('elevation_cli.requests.get')
def test_get_soil_type(mock_get):
    # Simulate a SoilGrids classification response
    soil_resp = {
        "most_probable": {"class_name": "Vertisol", "probability": 0.65},
        "classes": [
            {"class_name": "Vertisol", "probability": 0.65},
            {"class_name": "Luvisol", "probability": 0.2}
        ]
    }
    mock_get.return_value = DummyResponse(soil_resp)
    s = get_soil_type(0, 0, timeout=1)
    assert s['soil_provider'] == 'isric-soilgrids'
    assert s['soil_most_probable'] == 'Vertisol'
    assert isinstance(s['soil_most_probable_pct'], float)


@patch('elevation_cli.requests.get')
def test_get_soil_type_wrb_format(mock_get):
    soil_resp = {
        "wrb_class_name": "Phaeozems",
        "wrb_class_value": 20,
        "wrb_class_probability": [["Phaeozems", 27], ["Luvisols", 15]]
    }
    mock_get.return_value = DummyResponse(soil_resp)
    s = get_soil_type(0, 0, timeout=1)
    assert s['soil_provider'] == 'isric-soilgrids'
    assert s['soil_most_probable'] == 'Phaeozems'
    assert isinstance(s['soil_most_probable_pct'], float)


def test_worldclim_stub_no_data():
    from elevation_cli import get_worldclim_bio
    res = get_worldclim_bio(0, 0, data_dir=None, debug=True)
    assert res['worldclim_bio'] is None


def test_terraclimate_stub_no_data():
    from elevation_cli import get_terraclimate
    res = get_terraclimate(0, 0, data_dir=None, debug=True)
    assert res['terraclimate'] is None


def test_chirps_stub_no_data():
    from elevation_cli import get_chirps_stats
    res = get_chirps_stats(0, 0, data_dir=None, debug=True)
    assert res['chirps'] is None


def test_dem_stub_no_data():
    from elevation_cli import get_dem_derivatives
    res = get_dem_derivatives(0, 0, dem_path=None, debug=True)
    assert res['dem'] is None

def test_worldclim_sampling(monkeypatch, tmp_path):
    # Create fake raster files for BIO1..BIO3
    bio_files = {}
    for i in range(1, 4):
        p = tmp_path / f"wc_bio_{i}.tif"
        p.write_text('fake')
        bio_files[i] = str(p)

    # Mock glob.glob to return the fake file for patterns
    import glob

    def fake_glob(pattern, recursive=False):
        for i in range(1, 4):
            if f"bio*{i}" in pattern or f"_bio_{i}" in pattern or f"bio_{i}" in pattern:
                return [bio_files[i]]
        return []

    monkeypatch.setattr('glob.glob', fake_glob)

    # Mock rasterio with an open that returns an object with sample() yielding a value
    class FakeDS:
        def __init__(self, path):
            self.path = path

        def sample(self, coords):
            basename = self.path
            if 'bio_1' in basename or 'bio_1' in basename:
                yield [10.0]
            elif 'bio_2' in basename or 'bio_2' in basename:
                yield [20.0]
            elif 'bio_3' in basename or 'bio_3' in basename:
                yield [30.0]
            else:
                yield [0.0]

        def close(self):
            pass

    class FakeRasterio:
        def open(self, path):
            return FakeDS(path)

    # Insert fake rasterio into sys.modules so _try_import picks it up
    import sys as _sys
    _sys.modules['rasterio'] = FakeRasterio()

    from elevation_cli import get_worldclim_bio

    res = get_worldclim_bio(10.0, 20.0, data_dir=str(tmp_path), debug=True)
    assert 'worldclim_bio' in res
    bio = res['worldclim_bio']
    assert bio['BIO1'] == 10.0
    assert bio['BIO2'] == 20.0
    assert bio['BIO3'] == 30.0
