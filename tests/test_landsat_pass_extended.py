from __future__ import annotations

from datetime import date

import pytest

import utils.landsat_pass as landsat_pass

from tests.helpers import FakePoint, FakePolygon


def test_shapely_to_esri_json_supports_point_and_polygon():
    point_json, point_type = landsat_pass.shapely_to_esri_json(landsat_pass.Point(1, 2))
    poly_json, poly_type = landsat_pass.shapely_to_esri_json(
        landsat_pass.Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])
    )

    assert point_json in {"1,2", "1.0,2.0"}
    assert point_type == "esriGeometryPoint"
    assert '"rings"' in poly_json
    assert poly_type == "esriGeometryPolygon"


def test_build_cycle_sequence_and_path_mapping_validate_inputs():
    cycle_sequence = landsat_pass._build_cycle_sequence(
        {
            "landsat_8": {
                f"1/{index}/1970": {"cycle": index}
                for index in range(1, 17)
            }
        }
    )
    mission_paths = landsat_pass._build_mission_cycle_paths(
        {
            "landsat_8": {"1": [{"path": "101"}]},
            "landsat_9": {"2": [{"path": "102"}]},
        }
    )

    assert cycle_sequence == list(range(1, 17))
    assert mission_paths["landsat_8"][1] == {101}
    assert mission_paths["landsat_9"][2] == {102}

    with pytest.raises(KeyError):
        landsat_pass._build_cycle_sequence({"landsat_8": {"1/1/1970": {"cycle": 99}}})


def test_load_landsat_schedule_source_prefers_modern(monkeypatch):
    payloads = [
        {"landsat_8": {f"1/{index}/1970": {"cycle": index} for index in range(1, 17)}},
        {
            "landsat_8": {"1": [{"path": "101"}]},
            "landsat_9": {"2": [{"path": "102"}]},
        },
    ]

    monkeypatch.setattr(landsat_pass, "_fetch_json", lambda url, session: payloads.pop(0))

    result = landsat_pass.load_landsat_schedule_source(session=object())

    assert result.source == "modern"
    assert result.cycle_sequence == list(range(1, 17))
    assert result.mission_cycle_paths["landsat_8"][1] == {101}


def test_load_landsat_schedule_source_falls_back_to_legacy(monkeypatch):
    calls = {"count": 0}

    def fake_fetch(url, session):
        calls["count"] += 1
        if calls["count"] == 1:
            raise ValueError("bad modern payload")
        if calls["count"] == 2:
            return {
                "landsat_8": {"12/31/2025": {"path": "101"}},
                "landsat_9": {"12/30/2025": {"path": "102"}},
            }
        raise AssertionError("unexpected fetch")

    monkeypatch.setattr(landsat_pass, "_fetch_json", fake_fetch)

    result = landsat_pass.load_landsat_schedule_source(session=object())

    assert result.source == "legacy"
    assert "legacy Landsat schedule fallback" in result.warnings[0]
    assert result.latest_legacy_date == date(2025, 12, 31)


def test_load_landsat_schedule_source_marks_unavailable_when_all_fetches_fail(monkeypatch):
    def fake_fetch(url, session):
        raise landsat_pass.requests.RequestException("offline")

    monkeypatch.setattr(landsat_pass, "_fetch_json", fake_fetch)

    result = landsat_pass.load_landsat_schedule_source(session=object())

    assert result.source == "unavailable"
    assert "temporarily unavailable" in result.warnings[-1]


def test_ll2pr_parses_both_directions(monkeypatch):
    monkeypatch.setattr(
        landsat_pass,
        "shapely_to_esri_json",
        lambda geometry: ("geom", "esriGeometryPolygon"),
    )

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeSession:
        def __init__(self):
            self.calls = []

        def post(self, query_url, params=None, data=None, timeout=None):
            self.calls.append((query_url, params["where"], data))
            mode = params["where"]
            if mode == "MODE='A'":
                return FakeResponse({"features": [{"attributes": {"PATH": 101, "ROW": 22}, "geometry": {"rings": []}}]})
            return FakeResponse({"features": []})

    session = FakeSession()
    result = landsat_pass.ll2pr(FakePolygon("aoi"), session=session)

    assert result["ascending"][0]["path"] == 101
    assert result["descending"] is None
    assert len(session.calls) == 2


def test_ll2pr_handles_request_failures(monkeypatch):
    monkeypatch.setattr(
        landsat_pass,
        "shapely_to_esri_json",
        lambda geometry: ("geom", "esriGeometryPolygon"),
    )

    class FakeSession:
        def post(self, query_url, params=None, data=None, timeout=None):
            raise landsat_pass.requests.RequestException("boom")

    result = landsat_pass.ll2pr(FakePolygon("aoi"), session=FakeSession())

    assert result == {"ascending": None, "descending": None}


def test_next_landsat_pass_aggregates_geometry_and_warnings(monkeypatch):
    class FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(landsat_pass.requests, "Session", lambda: FakeSession())
    monkeypatch.setattr(
        landsat_pass,
        "ll2pr",
        lambda geometryAOI, session: {
            "ascending": [{"path": 101, "row": 7, "geometry": {"rings": []}}],
            "descending": None,
        },
    )
    monkeypatch.setattr(
        landsat_pass,
        "load_landsat_schedule_source",
        lambda session: landsat_pass.LandsatScheduleSource(source="modern"),
    )
    monkeypatch.setattr(
        landsat_pass,
        "arcgis_to_polygon",
        lambda geometry: FakePolygon("path-row", area=20.0),
    )
    monkeypatch.setattr(
        landsat_pass,
        "find_next_landsat_pass",
        lambda path, n_day_past, schedule_source, num_passes=5: (
            {"landsat_8": ["03/23/2026"], "landsat_9": ["03/27/2026"]},
            ["stale warning"],
        ),
    )
    monkeypatch.setattr(
        landsat_pass,
        "unary_union",
        lambda polygons: FakePolygon("merged", area=sum(p.area for p in polygons)),
    )
    monkeypatch.setattr(landsat_pass, "tabulate", lambda rows, headers=None, tablefmt=None: "formatted-table")

    result = landsat_pass.next_landsat_pass(
        lat=34.2,
        lon=-118.17,
        geometryAOI=FakePolygon("aoi", area=10.0),
        n_day_past=13,
    )

    assert result["next_collect_info"].startswith("formatted-table")
    assert "±15-40 minutes accuracy" in result["next_collect_info"]
    assert result["next_collect_geometry"][0].name == "merged"
    assert "Warning: stale warning" in result["next_collect_summary"][0]


def test_next_landsat_pass_includes_na_rows_when_no_path_rows(monkeypatch):
    captured = {}

    class FakeSession:
        def close(self):
            return None

    monkeypatch.setattr(landsat_pass.requests, "Session", lambda: FakeSession())
    monkeypatch.setattr(
        landsat_pass,
        "ll2pr",
        lambda geometryAOI, session: {"ascending": None, "descending": None},
    )
    monkeypatch.setattr(
        landsat_pass,
        "load_landsat_schedule_source",
        lambda session: landsat_pass.LandsatScheduleSource(source="modern"),
    )

    def fake_tabulate(rows, headers=None, tablefmt=None):
        captured["rows"] = rows
        return "table"

    monkeypatch.setattr(landsat_pass, "tabulate", fake_tabulate)

    result = landsat_pass.next_landsat_pass(
        lat=34.2,
        lon=-118.17,
        geometryAOI=FakePoint(1, 2),
        n_day_past=13,
    )

    assert captured["rows"][0][:3] == ["Ascending", "N/A", "N/A"]
    assert captured["rows"][1][:3] == ["Descending", "N/A", "N/A"]
    assert result["next_collect_geometry"] == []


def test_estimate_landsat_overpass_time_western_hemisphere_stays_on_same_day():
    """Los Angeles: 10:12 AM local ≈ 18:04 UTC — no day rollover."""
    from datetime import timezone

    result = landsat_pass.estimate_landsat_overpass_time("06/28/2026", 34.0, -118.0)

    assert result.tzinfo == timezone.utc
    assert result.date().isoformat() == "2026-06-28"
    assert result.hour == 18
    assert result.minute == 4


def test_estimate_landsat_overpass_time_far_eastern_rolls_to_previous_day():
    """Fiji / dateline: 10:12 AM local is late on the previous UTC day.

    Regression guard: the old implementation used `% 24` + `.replace(hour=...)`
    which kept the local calendar date, producing a date one day too late for
    far-eastern longitudes.
    """
    from datetime import timezone

    # lon = 175 (near dateline): utc_hour = 10.2 - 175/15 = -1.4666...
    # → previous day at 22:32 UTC
    result = landsat_pass.estimate_landsat_overpass_time("06/28/2026", -18.0, 175.0)

    assert result.tzinfo == timezone.utc
    assert result.date().isoformat() == "2026-06-27"
    assert result.hour == 22
    assert result.minute == 32


def test_estimate_landsat_overpass_time_midpacific_boundary():
    """Just past the rollover boundary — confirms the day flips correctly."""
    from datetime import timezone

    # lon = 155: utc_hour = 10.2 - 10.333 = -0.133 → previous day, 23:52 UTC
    result = landsat_pass.estimate_landsat_overpass_time("06/28/2026", 0.0, 155.0)

    assert result.tzinfo == timezone.utc
    assert result.date().isoformat() == "2026-06-27"
    assert result.hour == 23
    assert result.minute == 52
