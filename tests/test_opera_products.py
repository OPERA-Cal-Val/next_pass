from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone

import pytest

import utils.opera_products as opera_products
from tests.helpers import FakeFrame, FakePolygon


def test_describe_cloud_cover_thresholds():
    assert "mostly clear" in opera_products.describe_cloud_cover(25)
    assert "partly cloudy" in opera_products.describe_cloud_cover(60)
    assert "mostly cloudy" in opera_products.describe_cloud_cover(90)


def test_find_print_available_opera_products_validates_date_range(tmp_path):
    with pytest.raises(ValueError):
        opera_products.find_print_available_opera_products(
            ["34.2", "-118.17"], 2, "2026-03-10/2026-03-01", None, tmp_path
        )


def test_find_print_available_opera_products_prefixes_products_and_trims_dates(
    monkeypatch, tmp_path
):
    searches = []
    rows = FakeFrame(
        [
            {
                "BeginningDateTime": datetime(2026, 3, 20, 10, tzinfo=timezone.utc),
                "geometry": FakePolygon("g1"),
            },
            {
                "BeginningDateTime": datetime(2026, 3, 20, 12, tzinfo=timezone.utc),
                "geometry": FakePolygon("g2"),
            },
            {
                "BeginningDateTime": datetime(2026, 3, 18, 8, tzinfo=timezone.utc),
                "geometry": FakePolygon("g3"),
            },
        ]
    )

    def fake_search(short_name, cloud_hosted, bounding_box, temporal, return_gdf):
        searches.append(short_name)
        return (
            [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            rows.copy(),
        )

    monkeypatch.setattr(opera_products.leafmap, "nasa_data_search", fake_search)
    monkeypatch.setattr(opera_products, "bbox_type", lambda bbox: bbox)
    monkeypatch.setattr(
        opera_products,
        "bbox_to_geometry",
        lambda bbox, timestamp_dir: (FakePolygon("aoi"), [0, 1, 2, 3], None),
    )
    monkeypatch.setattr(opera_products.pd, "to_datetime", lambda values: values)
    monkeypatch.setattr(opera_products.time, "sleep", lambda seconds: None)

    result = opera_products.find_print_available_opera_products(
        bbox=[34.2, -118.17],
        number_of_dates=1,
        date_str="2026-03-23",
        list_of_products=["RTC-S1_V1", "DSWX-HLS_V1"],
        timestamp_dir=tmp_path,
    )

    assert searches == ["OPERA_L2_RTC-S1_V1", "OPERA_L3_DSWX-HLS_V1"]
    assert len(result["OPERA_L2_RTC-S1_V1"]["results"]) == 2


def test_export_opera_products_writes_workbook_and_skips_cloudiness_when_disabled(
    tmp_path,
):
    geometry = FakePolygon("geom")
    results_dict = {
        "OPERA_L3_DSWX-HLS_V1": {
            "results": [
                {
                    "umm": {
                        "GranuleUR": "granule-1",
                        "TemporalExtent": {
                            "RangeDateTime": {
                                "BeginningDateTime": "2026-03-20T00:00:00Z",
                                "EndingDateTime": "2026-03-20T01:00:00Z",
                            }
                        },
                        "RelatedUrls": [
                            {"URL": "https://example.com/file_B01_WTR.tif"},
                            {"URL": "https://example.com/file_CLOUD.tif"},
                        ],
                    }
                }
            ],
            "gdf": FakeFrame([{"geometry": geometry}]),
        }
    }

    opera_products.export_opera_products(
        results_dict,
        tmp_path,
        compute_cloudiness=False,
    )

    output_file = tmp_path / "opera_products_metadata.xlsx"
    assert output_file.exists()
    if zipfile.is_zipfile(output_file):
        from openpyxl import load_workbook

        workbook = load_workbook(output_file)
        sheet = workbook["OPERA Metadata"]
        assert sheet["A1"].value == "Dataset"
        assert sheet["B2"].value == "granule-1"
        assert sheet["F2"].value == "https://example.com/file_B01_WTR.tif"
    else:
        payload = json.loads(output_file.read_text(encoding="utf-8"))
        assert payload[0][0] == "Dataset"
        assert payload[1][1] == "granule-1"
        assert payload[1][5] == "https://example.com/file_B01_WTR.tif"
