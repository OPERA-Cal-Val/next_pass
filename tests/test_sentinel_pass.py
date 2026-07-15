from __future__ import annotations

from datetime import datetime, timezone

import utils.sentinel_pass as sentinel_pass

from tests.helpers import FakeFrame, FakePolygon


def test_unique_geometry_per_orbit_preserves_platform_groups(monkeypatch):
    monkeypatch.setattr(sentinel_pass.pd, "to_datetime", lambda value, format=None, errors=None: value)
    gdf = FakeFrame(
        [
            {
                "orbit_relative": 12,
                "platform": "S1A",
                "begin_date": datetime(2026, 3, 20, tzinfo=timezone.utc),
                "geometry": FakePolygon("g1"),
                "intersection_pct": 80.0,
            },
            {
                "orbit_relative": 12,
                "platform": "S1C",
                "begin_date": datetime(2026, 3, 21, tzinfo=timezone.utc),
                "geometry": FakePolygon("g2"),
                "intersection_pct": 70.0,
            },
        ]
    )

    result = sentinel_pass.unique_geometry_per_orbit(gdf)

    assert len(result.rows) == 2
    assert {row["platform"] for row in result.rows} == {"S1A", "S1C"}


def test_next_sentinel_pass_rejects_unknown_satellite():
    result = sentinel_pass.next_sentinel_pass("bad", FakePolygon("aoi"), 13, False)

    assert result["next_collect_info"] == "Unsupported satellite identifier."


def test_next_sentinel_pass_handles_plan_read_error(monkeypatch):
    monkeypatch.setattr(sentinel_pass.gpd, "read_file", lambda path: (_ for _ in ()).throw(OSError("missing")))
    monkeypatch.setattr(sentinel_pass, "create_s1_collection_plan", lambda n_day_past: "missing.geojson")

    result = sentinel_pass.next_sentinel_pass("sentinel1", FakePolygon("aoi"), 13, False)

    assert result["next_collect_info"] == "Error reading plan file."


def test_next_sentinel_pass_returns_grouped_results_without_cloudiness(monkeypatch):
    monkeypatch.setattr(sentinel_pass, "create_s1_collection_plan", lambda n_day_past: "collection.geojson")
    monkeypatch.setattr(sentinel_pass.gpd, "read_file", lambda path: FakeFrame([{"platform": "S1A"}]))
    monkeypatch.setattr(
        sentinel_pass,
        "find_intersecting_collects",
        lambda gdf, geometry: FakeFrame(
            [
                {
                    "begin_date": datetime(2026, 3, 20, tzinfo=timezone.utc),
                    "orbit_relative": 44,
                    "platform": "S1A",
                    "geometry": FakePolygon("geom"),
                    "intersection_pct": 77.0,
                },
                {
                    "begin_date": datetime(2026, 3, 20, tzinfo=timezone.utc),
                    "orbit_relative": 44,
                    "platform": "S1A",
                    "geometry": FakePolygon("geom"),
                    "intersection_pct": 77.0,
                },
            ]
        ),
    )
    monkeypatch.setattr(
        sentinel_pass,
        "unique_geometry_per_orbit",
        lambda collects: FakeFrame(
            [
                {
                    "begin_date": [datetime(2026, 3, 20, tzinfo=timezone.utc)],
                    "orbit_relative": 44,
                    "platform": "S1A",
                    "geometry": FakePolygon("geom"),
                    "intersection_pct": 77.0,
                }
            ]
        ),
    )
    monkeypatch.setattr(sentinel_pass, "format_collects", lambda grouped: "table")
    monkeypatch.setattr(sentinel_pass, "build_collect_summaries", lambda grouped: ["summary"])

    result = sentinel_pass.next_sentinel_pass("sentinel1", FakePolygon("aoi"), 13, False)

    assert result["next_collect_info"] == "table"
    assert result["intersection_pct"] == [77.0]
    assert result["next_collect_summary"] == ["summary"]


def test_next_sentinel_pass_returns_cloudiness_when_requested(monkeypatch):
    monkeypatch.setattr(sentinel_pass, "create_s2_collection_plan", lambda n_day_past: "collection.geojson")
    monkeypatch.setattr(sentinel_pass.gpd, "read_file", lambda path: FakeFrame([{}]))
    monkeypatch.setattr(
        sentinel_pass,
        "find_intersecting_collects",
        lambda gdf, geometry: FakeFrame(
            [
                {
                    "begin_date": datetime(2026, 3, 20, tzinfo=timezone.utc),
                    "orbit_relative": 51,
                    "geometry": FakePolygon("geom"),
                    "intersection_pct": 40.0,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        sentinel_pass,
        "make_get_cloudiness_for_row",
        lambda geometry: (lambda row: [12.5]),
    )
    monkeypatch.setattr(sentinel_pass, "format_collects", lambda grouped: "cloudy-table")
    monkeypatch.setattr(sentinel_pass, "build_collect_summaries", lambda grouped: ["cloudy-summary"])

    result = sentinel_pass.next_sentinel_pass("sentinel2", FakePolygon("aoi"), 13, True)

    assert result["next_collect_info"] == "cloudy-table"
    assert result["cloudiness"] == [[12.5]]


def test_next_sentinel_pass_returns_no_collect_message(monkeypatch):
    monkeypatch.setattr(sentinel_pass, "create_s1_collection_plan", lambda n_day_past: "collection.geojson")
    monkeypatch.setattr(
        sentinel_pass.gpd,
        "read_file",
        lambda path: FakeFrame([{"end_date": datetime(2026, 3, 30, tzinfo=timezone.utc)}]),
    )
    monkeypatch.setattr(
        sentinel_pass,
        "find_intersecting_collects",
        lambda gdf, geometry: FakeFrame([]),
    )

    result = sentinel_pass.next_sentinel_pass("sentinel1", FakePolygon("aoi"), 13, False)

    assert "No scheduled collects before 2026-03-30" in result["next_collect_info"]


def test_next_sentinel_pass_returns_tide_for_point_aoi(monkeypatch):
    """Regression test: --tide must work for point AOIs like -b 34.20 -118.17."""
    from tests.helpers import FakePoint

    monkeypatch.setattr(sentinel_pass, "create_s1_collection_plan", lambda n_day_past: "collection.geojson")
    monkeypatch.setattr(sentinel_pass.gpd, "read_file", lambda path: FakeFrame([{"platform": "S1A"}]))
    monkeypatch.setattr(
        sentinel_pass,
        "find_intersecting_collects",
        lambda gdf, geometry: FakeFrame(
            [
                {
                    "begin_date": datetime(2026, 3, 20, tzinfo=timezone.utc),
                    "orbit_relative": 51,
                    "geometry": FakePolygon("geom"),
                    "intersection_pct": 40.0,
                }
            ]
        ),
    )
    monkeypatch.setattr(
        sentinel_pass,
        "get_tide_info_batch",
        lambda polygon, target_isos, station_dicts, allow_interpolation: [
            {"nearest": "1.23(H-rising)", "per_station": {"9432780": "1.23(H-rising)"}}
            for _ in target_isos
        ],
    )
    monkeypatch.setattr(sentinel_pass, "format_collects", lambda grouped: "tide-table")
    monkeypatch.setattr(sentinel_pass, "build_collect_summaries", lambda grouped: ["tide-summary"])
    monkeypatch.setattr(sentinel_pass, "get_stations_in_aoi", lambda geom: [{"id": "9432780", "name": "LA", "lat": 34.0, "lng": -118.0}])

    point_aoi = FakePoint(-118.17, 34.20)
    result = sentinel_pass.next_sentinel_pass("sentinel1", point_aoi, 13, False, arg_tide=True)

    assert result["tide"] == [[{"nearest": "1.23(H-rising)", "per_station": {"9432780": "1.23(H-rising)"}}]]
    assert result["noaa_stations"] == [{"id": "9432780", "name": "LA", "lat": 34.0, "lng": -118.0}]

