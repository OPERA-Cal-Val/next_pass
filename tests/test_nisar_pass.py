from __future__ import annotations

import zipfile
from datetime import datetime, timedelta, timezone

import utils.nisar_pass as nisar_pass

from tests.helpers import FakeFrame, FakePolygon


def test_parse_nisar_description_extracts_products_and_attributes():
    html = """
    <table>
      <tr><th>track</th><td>145</td></tr>
      <tr><th>frame</th><td>17</td></tr>
      <tr><th>passDirection</th><td>Ascending</td></tr>
      <tr><th>2026-03-20</th><td>S_37</td></tr>
      <tr><th>2026-03-25</th><td>S_75</td></tr>
    </table>
    """

    products, attrs = nisar_pass.parse_nisar_description(html)

    assert attrs == {"track": "145", "frame": "17", "passDirection": "Ascending"}
    assert products[0][0] == datetime(2026, 3, 20, tzinfo=timezone.utc)
    assert products[1][1] == "S_75"


def test_iter_nisar_placemarks_reads_track_and_frame_from_name(tmp_path):
    kmz_path = tmp_path / "plan.kmz"
    doc_kml = """
    <kml xmlns="http://www.opengis.net/kml/2.2">
      <Placemark>
        <name>T145_F017</name>
        <description><![CDATA[
          <table>
            <tr><th>passDirection</th><td>Descending</td></tr>
            <tr><th>2026-03-20</th><td>S_37</td></tr>
          </table>
        ]]></description>
        <Polygon>
          <outerBoundaryIs>
            <LinearRing>
              <coordinates>0,0 1,0 1,1 0,0</coordinates>
            </LinearRing>
          </outerBoundaryIs>
        </Polygon>
      </Placemark>
    </kml>
    """
    with zipfile.ZipFile(kmz_path, "w") as archive:
        archive.writestr("doc.kml", doc_kml)

    records = list(nisar_pass.iter_nisar_placemarks(kmz_path))

    assert len(records) == 1
    assert records[0]["track"] == 145
    assert records[0]["frame"] == 17
    assert records[0]["pass_direction"] == "Descending"


def test_create_nisar_collection_plan_returns_empty_path_when_no_rows(monkeypatch, tmp_path):
    monkeypatch.setattr(nisar_pass, "SCRATCH_DIR", tmp_path)
    monkeypatch.setattr(nisar_pass, "download_nisar_plan", lambda url, output_path: tmp_path / "plan.kmz")
    monkeypatch.setattr(nisar_pass, "iter_nisar_placemarks", lambda kmz_path: iter(()))

    result = nisar_pass.create_nisar_collection_plan()

    assert result == nisar_pass.Path()


def test_next_nisar_pass_handles_read_failures(monkeypatch):
    monkeypatch.setattr(nisar_pass, "create_nisar_collection_plan", lambda: (_ for _ in ()).throw(OSError("bad file")))

    result = nisar_pass.next_nisar_pass(FakePolygon("aoi"), 13)

    assert result["next_collect_info"] == "Error reading NISAR plan file."


def test_next_nisar_pass_returns_no_collect_message(monkeypatch):
    end_date = datetime.now(timezone.utc) + timedelta(days=4)
    monkeypatch.setattr(nisar_pass, "create_nisar_collection_plan", lambda: "nisar.geojson")
    monkeypatch.setattr(
        nisar_pass.gpd,
        "read_file",
        lambda path: FakeFrame(
            [{"begin_date": end_date, "end_date": end_date, "geometry": FakePolygon("geom")}]
        ),
    )
    monkeypatch.setattr(nisar_pass.pd, "to_datetime", lambda value, utc=True, errors=None: value)
    monkeypatch.setattr(nisar_pass, "find_intersecting_collects", lambda gdf, geometry: FakeFrame([]))

    result = nisar_pass.next_nisar_pass(FakePolygon("aoi"), 13)

    assert "No scheduled collects before" in result["next_collect_info"]


def test_next_nisar_pass_groups_by_best_overlap(monkeypatch):
    now = datetime.now(timezone.utc)
    rows = FakeFrame(
        [
            {
                "begin_date": now,
                "end_date": now,
                "geometry": FakePolygon("g1"),
                "track": 12,
                "frame": 1,
                "pass_direction": "Ascending",
                "radar_mode": "S_37",
                "intersection_pct": 60.0,
            },
            {
                "begin_date": now + timedelta(days=1),
                "end_date": now + timedelta(days=1),
                "geometry": FakePolygon("g2"),
                "track": 12,
                "frame": 2,
                "pass_direction": "Ascending",
                "radar_mode": "S_75",
                "intersection_pct": 90.0,
            },
        ]
    )

    monkeypatch.setattr(nisar_pass, "create_nisar_collection_plan", lambda: "nisar.geojson")
    monkeypatch.setattr(nisar_pass.gpd, "read_file", lambda path: rows.copy())
    monkeypatch.setattr(nisar_pass.pd, "to_datetime", lambda value, utc=True, errors=None: value)
    monkeypatch.setattr(nisar_pass, "find_intersecting_collects", lambda gdf, geometry: gdf)
    monkeypatch.setattr(nisar_pass, "format_collects", lambda grouped: "table")
    monkeypatch.setattr(nisar_pass, "build_collect_summaries", lambda grouped: ["summary"] * len(grouped.rows))

    result = nisar_pass.next_nisar_pass(FakePolygon("aoi"), 13)

    assert result["next_collect_info"].startswith("table")
    assert "±20-40 minutes accuracy" in result["next_collect_info"]
    assert result["intersection_pct"] == [90.0]


def test_estimate_nisar_overpass_time_western_descending_rolls_forward_one_day():
    """Los Angeles descending: 6 PM local ≈ 01:52 UTC on the following day.

    Regression guard: the old implementation used `% 24` + `.replace(hour=...)`
    which produced 02:00 UTC on the SAME day for LA descending — off by 24 hours
    and causing tide lookups to fetch the wrong day.
    """
    result = nisar_pass.estimate_nisar_overpass_time(
        "2026-06-28", 34.0, -118.0, "Descending"
    )

    assert result.tzinfo == timezone.utc
    assert result.date().isoformat() == "2026-06-29"
    assert result.hour == 1
    assert result.minute == 52


def test_estimate_nisar_overpass_time_eastern_ascending_rolls_back_one_day():
    """Tokyo ascending: 6 AM local ≈ 20:40 UTC on the previous day.

    Regression guard: the old implementation produced 20:40 UTC on the SAME
    calendar day for eastern-hemisphere ascending passes — off by 24 hours.
    """
    # lon = 140: utc_hour = 6 - 140/15 = -3.333... → previous day, 20:40 UTC
    result = nisar_pass.estimate_nisar_overpass_time(
        "2026-06-28", 35.0, 140.0, "Ascending"
    )

    assert result.tzinfo == timezone.utc
    assert result.date().isoformat() == "2026-06-27"
    assert result.hour == 20
    assert result.minute == 40


def test_estimate_nisar_overpass_time_prime_meridian_no_rollover():
    """Sanity check: ascending pass at longitude 0 stays on the same UTC day."""
    result = nisar_pass.estimate_nisar_overpass_time(
        "2026-06-28", 0.0, 0.0, "Ascending"
    )

    assert result.tzinfo == timezone.utc
    assert result.date().isoformat() == "2026-06-28"
    assert result.hour == 6
    assert result.minute == 0


def test_estimate_nisar_overpass_time_dateline_descending_double_rollover():
    """Far western dateline descending: rolls forward one full UTC day."""
    # lon = -175: utc_hour = 18 - (-175/15) = 18 + 11.666... = 29.666...
    # → next day at 05:40 UTC
    result = nisar_pass.estimate_nisar_overpass_time(
        "2026-06-28", -18.0, -175.0, "Descending"
    )

    assert result.tzinfo == timezone.utc
    assert result.date().isoformat() == "2026-06-29"
    assert result.hour == 5
    assert result.minute == 40

