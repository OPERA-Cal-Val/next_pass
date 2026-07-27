from __future__ import annotations

from datetime import datetime, timezone

import utils.plot_maps as plot_maps
from tests.helpers import FakeFrame, FakePolygon


def _make_polygon(module, name="poly"):
    try:
        return module.Polygon([(0, 0), (1, 0), (1, 1), (0, 0)])
    except Exception:
        return FakePolygon(name)


def _make_map_gdf(module, polygon):
    try:
        gdf = module.gpd.GeoDataFrame(
            {"URL": [""], "GranuleUR": [""], "geometry": [polygon]},
            geometry="geometry",
            crs="EPSG:4326",
        )
        if hasattr(gdf, "iloc") and hasattr(gdf, "reset_index"):
            return gdf
        return FakeFrame([{"geometry": polygon, "URL": "", "GranuleUR": ""}])
    except Exception:
        return FakeFrame([{"geometry": polygon, "URL": "", "GranuleUR": ""}])


def test_make_opera_granule_map_creates_output(tmp_path, monkeypatch):
    polygon = _make_polygon(plot_maps, "g1")
    gdf = _make_map_gdf(plot_maps, polygon)
    results = {
        "OPERA_L3_DSWX-HLS_V1": {
            "gdf": gdf,
            "results": [
                {
                    "umm": {
                        "GranuleUR": "granule-1",
                        "RelatedUrls": [
                            {"Type": "GET DATA", "URL": "https://example.com/granule"}
                        ],
                    }
                }
            ],
        }
    }

    monkeypatch.setattr(plot_maps, "bbox_type", lambda bbox: bbox)
    monkeypatch.setattr(
        plot_maps,
        "bbox_to_geometry",
        lambda bbox, timestamp_dir: (
            _make_polygon(plot_maps, "aoi"),
            None,
            type("C", (), {"x": 1, "y": 2})(),
        ),
    )

    plot_maps.make_opera_granule_map(results, [34.2, -118.17], tmp_path)

    assert (tmp_path / "opera_products_map.html").exists()


def test_make_opera_granule_drcs_map_handles_old_granules(tmp_path, monkeypatch):
    polygon = _make_polygon(plot_maps, "g1")
    gdf = _make_map_gdf(plot_maps, polygon)
    results = {
        "OPERA_L3_DSWX-S1_V1": {
            "gdf": gdf,
            "results": [
                {
                    "umm": {
                        "GranuleUR": "OPERA_L3_DSWX-S1_T001_F001_20260320T000000Z",
                        "RelatedUrls": [],
                    }
                }
            ],
        }
    }

    monkeypatch.setattr(plot_maps, "bbox_type", lambda bbox: bbox)
    monkeypatch.setattr(
        plot_maps,
        "bbox_to_geometry",
        lambda bbox, timestamp_dir: (
            _make_polygon(plot_maps, "aoi"),
            None,
            type("C", (), {"x": 1, "y": 2})(),
        ),
    )
    monkeypatch.setattr(
        plot_maps, "check_opera_overpass_intersection", lambda *args, **kwargs: "report"
    )

    plot_maps.make_opera_granule_drcs_map(
        datetime(2026, 3, 21, tzinfo=timezone.utc),
        results,
        result_s1={},
        result_s2={},
        result_l={},
        bbox=[34.2, -118.17],
        timestamp_dir=tmp_path,
    )

    assert (tmp_path / "opera_products_drcs_map.html").exists()


def test_make_overpasses_map_creates_output(tmp_path, monkeypatch):
    monkeypatch.setattr(plot_maps, "bbox_type", lambda bbox: bbox)
    monkeypatch.setattr(
        plot_maps,
        "bbox_to_geometry",
        lambda bbox, timestamp_dir: (
            _make_polygon(plot_maps, "aoi"),
            None,
            type("C", (), {"x": 1, "y": 2})(),
        ),
    )
    monkeypatch.setattr(
        plot_maps, "hsl_distinct_colors_improved", lambda n: ["#111111"] * max(n, 1)
    )

    result_s1 = {
        "next_collect_info": "info",
        "next_collect_geometry": [_make_polygon(plot_maps, "s1")],
        "next_collect_summary": ["summary"],
    }
    result_l = {
        "next_collect_info": "info",
        "next_collect_geometry": [
            _make_polygon(plot_maps, "l8"),
            _make_polygon(plot_maps, "l9"),
        ],
        "next_collect_summary": ["Ascending mission", "Descending mission"],
    }

    plot_maps.make_overpasses_map(
        result_s1, None, result_l, None, [34.2, -118.17], tmp_path
    )

    assert (tmp_path / "satellite_overpasses_map.html").exists()
