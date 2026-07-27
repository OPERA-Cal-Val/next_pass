from __future__ import annotations

from datetime import datetime, timedelta, timezone

import utils.cloudiness as cloudiness
from tests.helpers import FakePolygon


class FakeResponse:
    def __init__(self, payload, status_code=200, text=""):
        self._payload = payload
        self.status_code = status_code
        self.text = text
        self.headers = {}

    def raise_for_status(self):
        if self.status_code >= 400:
            error = cloudiness.requests.exceptions.HTTPError("bad response")
            error.response = self
            raise error

    def json(self):
        return self._payload


def test_as_utc_datetime_normalizes_naive_and_aware_values():
    naive = cloudiness.as_utc_datetime("2026-03-23T10:00:00")
    aware = cloudiness.as_utc_datetime(
        datetime(2026, 3, 23, 10, 0, tzinfo=timezone.utc)
    )

    assert naive.tzinfo == timezone.utc
    assert aware.tzinfo == timezone.utc


def test_get_cloudiness_at_point_exact_and_nearest():
    session = type(
        "Session",
        (),
        {
            "get": lambda self, url, params=None, timeout=None: FakeResponse(
                {
                    "hourly": {
                        "time": ["2026-03-23T10:00", "2026-03-23T11:00"],
                        "cloudcover": [10, 70],
                    }
                }
            )
        },
    )()

    assert (
        cloudiness.get_cloudiness_at_point(1, 2, "2026-03-23T10:00", session=session)
        == 10
    )
    assert cloudiness.get_cloudiness_at_point(
        1,
        2,
        "2026-03-23T10:30",
        allow_nearest=True,
        session=session,
    ) in {10, 70}


def test_get_cloudiness_at_points_sets_api_limit_on_429():
    cloudiness.hit_api_limit = False

    session = type(
        "Session",
        (),
        {
            "get": lambda self, url, params=None, timeout=None: FakeResponse(
                {}, status_code=429, text='{"reason": "too many requests"}'
            )
        },
    )()

    result = cloudiness.get_cloudiness_at_points(
        [(1, 2), (3, 4)], "2026-03-23T10:00", session=session
    )

    assert result == [None, None]
    assert cloudiness.hit_api_limit is True

    cloudiness.hit_api_limit = False


def test_get_historical_cloudiness_at_points_returns_values():
    session = type(
        "Session",
        (),
        {
            "get": lambda self, url, params=None, timeout=None: FakeResponse(
                {
                    "hourly": {
                        "time": ["2026-03-20T10:00", "2026-03-20T11:00"],
                        "cloudcover": [12, 18],
                    }
                }
            )
        },
    )()

    result = cloudiness.get_historical_cloudiness_at_points(
        [(1, 2)],
        "2026-03-20T11:00",
        session=session,
    )

    assert result == [18]


def test_get_overpass_cloudiness_chooses_future_and_historical_backends(monkeypatch):
    cloudiness.hit_api_limit = False
    monkeypatch.setattr(
        cloudiness,
        "shape",
        lambda geojson: FakePolygon("aoi", area=10.0),
    )
    monkeypatch.setattr(
        cloudiness,
        "generate_grid_sample_points",
        lambda polygon, num_points=10: [type("P", (), {"x": 1, "y": 2})()],
    )
    monkeypatch.setattr(
        cloudiness, "generate_random_sample_points", lambda polygon, n=10: []
    )
    monkeypatch.setattr(
        cloudiness,
        "get_cloudiness_at_points",
        lambda points, target_iso, allow_nearest=False: [10],
    )
    monkeypatch.setattr(
        cloudiness,
        "get_historical_cloudiness_at_points",
        lambda points, target_iso, allow_nearest=False: [20],
    )

    future = cloudiness.get_overpass_cloudiness(
        {"type": "Polygon", "coordinates": []},
        datetime.now(timezone.utc) + timedelta(days=1),
        sampling_method="grid",
    )
    past = cloudiness.get_overpass_cloudiness(
        {"type": "Polygon", "coordinates": []},
        datetime.now(timezone.utc) - timedelta(days=1),
        sampling_method="grid",
    )

    assert future == 10
    assert past == 20


def test_make_get_cloudiness_for_row_respects_14_day_limit(monkeypatch):
    captured = []
    monkeypatch.setattr(
        cloudiness,
        "get_overpass_cloudiness",
        lambda polygon_geojson, target_datetime, num_samples, allow_nearest, sampling_method: captured.append(
            num_samples
        )
        or 33.0,
    )

    get_for_row = cloudiness.make_get_cloudiness_for_row(FakePolygon("aoi"))
    row = type(
        "Row",
        (),
        {
            "begin_date": [
                datetime.now(timezone.utc) + timedelta(days=2),
                datetime.now(timezone.utc) + timedelta(days=20),
            ],
            "geometry": FakePolygon("collect"),
        },
    )()

    values = get_for_row(row)

    assert values[0] == 33.0
    assert values[1] is None
    assert captured[0] == 210
