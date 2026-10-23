import json
from datetime import UTC, datetime, timedelta

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from next_pass import schedule
from next_pass.landsat_pass import LandsatScheduleSource

TODAY = datetime(2026, 10, 22, tzinfo=UTC)


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(schedule, "_today", lambda: TODAY)


def test_clear_schedule_cache_preserves_unrelated_files(tmp_path):
    owned_files = [
        "sentinel1_plan.kml",
        "sentinel2_plan.geojson",
        "sentinel_1_collection.geojson",
        "sentinel_2_collection.geojson",
        "nisar_observation_plan.kmz",
        "nisar_collection.geojson",
    ]
    for name in owned_files:
        (tmp_path / name).write_text("cache", encoding="utf-8")

    unrelated = tmp_path / "user-data.txt"
    unrelated.write_text("keep", encoding="utf-8")
    nested = tmp_path / "user-directory"
    nested.mkdir()
    (nested / "keep.txt").write_text("keep", encoding="utf-8")

    schedule.clear_schedule_cache(tmp_path)

    assert all(not (tmp_path / name).exists() for name in owned_files)
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert (nested / "keep.txt").read_text(encoding="utf-8") == "keep"


def _sentinel_plan(tmp_path, offsets_days):
    begins = [TODAY + timedelta(days=d, hours=6) for d in offsets_days]
    gdf = gpd.GeoDataFrame(
        {
            "platform": "S1A",
            "mode": "IW",
            "orbit_relative": 12,
            "begin_date": [b.isoformat() for b in begins],
            "end_date": [(b + timedelta(minutes=1)).isoformat() for b in begins],
        },
        geometry=[box(0, 0, 1, 1)] * len(begins),
        crs="EPSG:4326",
    )
    path = tmp_path / "plan.geojson"
    gdf.to_file(path)
    return path


@pytest.mark.parametrize(
    ("days_back", "expected_offsets", "expected_n_day_past"),
    [(0, [0, 6], 0), (14, [-14, -1, 0, 6], 15)],
)
def test_sentinel_lookback_window(
    tmp_path, monkeypatch, days_back, expected_offsets, expected_n_day_past
):
    plan = _sentinel_plan(tmp_path, [-15, -14, -1, 0, 6, 7])
    calls = []

    def fake_builder(n_day_past):
        calls.append(n_day_past)
        return plan

    monkeypatch.setattr(schedule, "create_s1_collection_plan", fake_builder)
    out = schedule.build_sentinel_schedule("sentinel-1", 7, days_back)

    assert calls == [expected_n_day_past]
    got = sorted((out["begin_time"] - TODAY).dt.days.tolist())
    assert got == expected_offsets


def test_landsat_lookback_covers_past_days(monkeypatch):
    source = LandsatScheduleSource(
        source="modern",
        cycle_sequence=list(range(1, 17)),
        mission_cycle_paths={
            "landsat_8": {c: {1} for c in range(1, 17)},
            "landsat_9": {},
        },
    )
    monkeypatch.setattr(schedule, "load_landsat_schedule_source", lambda s: source)
    monkeypatch.setattr(
        schedule,
        "estimate_landsat_overpass_time",
        lambda day, lat, lon, pass_direction=None: datetime.strptime(
            day, "%m/%d/%Y"
        ).replace(hour=15, tzinfo=UTC),
    )
    wrs2 = gpd.GeoDataFrame(
        {"path": [1], "row": [1], "direction": ["descending"]},
        geometry=[box(0, 0, 1, 1)],
        crs="EPSG:4326",
    )

    out = schedule.build_landsat_schedule(3, session=object(), wrs2=wrs2, days_back=2)

    days = sorted(pd.to_datetime(out["begin_time"]).dt.date.tolist())
    assert days == [(TODAY + timedelta(days=d)).date() for d in range(-2, 3)]


def test_nisar_ignores_lookback(monkeypatch):
    called = {}

    def fake_build_nisar(days):
        called["days"] = days
        return schedule._empty_schedule()

    monkeypatch.setattr(schedule, "build_nisar_schedule", fake_build_nisar)
    schedule.build_schedule(["nisar"], 7, days_back=14)
    assert called == {"days": 7}


def test_meta_records_history_window(tmp_path):
    out = schedule.write_schedule(
        schedule._empty_schedule(), tmp_path / "p.parquet", 7, days_back=14
    )
    meta = json.loads(out.with_suffix(".meta.json").read_text())
    assert meta["days_back"] == 14
    assert meta["history_start"] == (TODAY - timedelta(days=14)).isoformat()
    assert meta["history_sensors"] == schedule.HISTORY_SENSORS


def test_cli_rejects_negative_days_back():
    with pytest.raises(SystemExit):
        schedule.main(["--days-back", "-1", "--no-refresh"])
