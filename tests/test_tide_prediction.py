from __future__ import annotations

import json

from shapely.geometry import Point, Polygon

import utils.tide_prediction as tide_prediction


def test_ensure_station_cache_defaults_to_scratch(monkeypatch, tmp_path):
    payload = {
        "stations": [{"id": "9432780", "name": "LA", "lat": "34.0", "lng": "-118.0"}]
    }

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(tide_prediction, "SCRATCH_DIR", tmp_path)
    monkeypatch.setattr(
        tide_prediction.requests, "get", lambda *args, **kwargs: FakeResponse()
    )
    monkeypatch.setattr(tide_prediction, "_STATIONS_CACHE", None)

    tide_prediction.ensure_station_cache()

    cache_path = tmp_path / "noaa_stations.json"
    assert cache_path.exists()
    assert json.loads(cache_path.read_text(encoding="utf-8")) == payload


def test_get_stations_in_aoi_point_returns_nearest_three_within_50km(monkeypatch):
    monkeypatch.setattr(
        tide_prediction, "ensure_station_cache", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        tide_prediction,
        "get_stations",
        lambda *args, **kwargs: [
            {"id": "near-1", "name": "Near 1", "lat": "34.20", "lng": "-118.17"},
            {"id": "near-2", "name": "Near 2", "lat": "34.35", "lng": "-118.17"},
            {"id": "near-3", "name": "Near 3", "lat": "34.55", "lng": "-118.17"},
            {"id": "far", "name": "Far", "lat": "35.10", "lng": "-118.17"},
        ],
    )

    stations = tide_prediction.get_stations_in_aoi(Point(-118.17, 34.20))

    assert [station["id"] for station in stations] == ["near-1", "near-2", "near-3"]


def test_get_stations_in_aoi_polygon_falls_back_to_nearby_only(monkeypatch):
    monkeypatch.setattr(
        tide_prediction, "ensure_station_cache", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        tide_prediction,
        "get_stations",
        lambda *args, **kwargs: [
            {"id": "near-1", "name": "Near 1", "lat": "34.20", "lng": "-118.30"},
            {"id": "near-2", "name": "Near 2", "lat": "34.20", "lng": "-118.40"},
            {"id": "far", "name": "Far", "lat": "34.20", "lng": "-119.00"},
        ],
    )

    polygon = Polygon(
        [
            (-118.17, 34.18),
            (-118.15, 34.18),
            (-118.15, 34.22),
            (-118.17, 34.22),
            (-118.17, 34.18),
        ]
    )
    stations = tide_prediction.get_stations_in_aoi(polygon)

    assert [station["id"] for station in stations] == ["near-1", "near-2"]
