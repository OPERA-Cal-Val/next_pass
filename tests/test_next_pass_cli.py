from __future__ import annotations

import argparse
import subprocess
import sys
import types
from pathlib import Path

import next_pass

from tests.helpers import FakePoint, FakePolygon


ROOT = Path(__file__).resolve().parents[1]


def test_create_parser_defaults():
    parser = next_pass.create_parser()

    args = parser.parse_args(["-b", "34.2", "-118.17"])

    assert args.sat == "all"
    assert args.look_back == 13
    assert args.functionality == "both"
    assert args.number_of_dates == 5
    assert args.event_date == "today"
    assert args.cloudiness is False


def test_create_parser_accepts_readme_bbox_example():
    parser = next_pass.create_parser()

    args = parser.parse_args(["-b", "34.15", "34.25", "-118.20", "-118.15"])

    assert args.bbox == ["34.15", "34.25", "-118.20", "-118.15"]
    assert args.sat == "all"


def test_bbox_example_command_runs_in_subprocess(tmp_path):
    script = f"""
import runpy
from pathlib import Path

root = Path({str(ROOT)!r})
tmpdir = Path({str(tmp_path)!r})

runpy.run_path(str(root / "tests" / "conftest.py"), run_name="__cli_stubs__")

import next_pass
import utils.cloudiness as cloudiness
import utils.landsat_pass as landsat_pass
import utils.nisar_pass as nisar_pass
import utils.opera_products as opera_products
import utils.plot_maps as plot_maps
import utils.sentinel_pass as sentinel_pass
import utils.utils as utils_mod
from tests.helpers import FakePoint, FakePolygon


class FakeDateTime:
    @classmethod
    def now(cls):
        class _Now:
            def strftime(self, _fmt):
                return "20260323_101010"

        return _Now()


def fake_bbox_to_geometry(bbox, timestamp_dir):
    return (
        FakePolygon("aoi", centroid_x=-118.175, centroid_y=34.20),
        (),
        FakePoint(-118.175, 34.20),
    )


def fake_make_overpasses_map(result_s1, result_s2, result_l, result_nisar, bbox, timestamp_dir):
    (timestamp_dir / "satellite_overpasses_map.html").write_text("<html></html>", encoding="utf-8")


def fake_make_opera_granule_map(results_dict, bbox, timestamp_dir):
    (timestamp_dir / "opera_products_map.html").write_text("<html></html>", encoding="utf-8")


next_pass.datetime = FakeDateTime
cloudiness.api_limit_reached = lambda: True
utils_mod.bbox_type = lambda bbox: bbox
utils_mod.bbox_to_geometry = fake_bbox_to_geometry
sentinel_pass.next_sentinel_pass = lambda sat, geometry, n_day_past, pred_cloudiness, pred_tide=False: {{
    "next_collect_info": sat,
    "next_collect_geometry": [geometry],
    "next_collect_summary": [sat],
}}
nisar_pass.next_nisar_pass = lambda geometry, n_day_past, arg_tide=False: {{
    "next_collect_info": "nisar",
    "next_collect_geometry": [geometry],
    "next_collect_summary": ["nisar"],
}}
landsat_pass.next_landsat_pass = lambda lat, lon, geometry, n_day_past, arg_tide=False: {{
    "next_collect_info": "landsat",
    "next_collect_geometry": [geometry],
    "next_collect_summary": ["landsat"],
}}
opera_products.find_print_available_opera_products = lambda *args, **kwargs: {{}}
opera_products.export_opera_products = lambda *args, **kwargs: None
plot_maps.make_overpasses_map = fake_make_overpasses_map
plot_maps.make_opera_granule_map = fake_make_opera_granule_map
plot_maps.make_opera_granule_drcs_map = lambda *args, **kwargs: None

import os
os.chdir(tmpdir)
output_dir = next_pass.main(["-b", "34.15", "34.25", "-118.20", "-118.15"])
print(f"OUTPUT={{output_dir}}")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    output_dir = tmp_path / "nextpass_outputs_20260323_101010"
    assert output_dir.exists()
    assert (output_dir / "run_output.txt").exists()
    assert (output_dir / "satellite_overpasses_map.html").exists()
    assert (output_dir / "opera_products_map.html").exists()
    assert "OUTPUT=nextpass_outputs_20260323_101010" in result.stdout


def test_format_arg_accepts_strings_and_lists():
    assert next_pass.format_arg("POINT (-118 34)") == "POINT (-118 34)"
    assert next_pass.format_arg(["34.2", "-118.17"]) == "34.2 -118.17"


def test_format_arg_rejects_invalid_inputs():
    try:
        next_pass.format_arg(("34.2", "-118.17"))
    except ValueError as error:
        assert "1, 2, or 4 strings" in str(error)
    else:
        raise AssertionError("format_arg should reject tuples")


def test_run_next_pass_builds_cli_args(monkeypatch):
    captured = {}

    def fake_main(cli_args):
        captured["cli_args"] = cli_args
        return "ok"

    monkeypatch.setattr(next_pass, "main", fake_main)

    result = next_pass.run_next_pass(
        bbox=[34.2, 34.3, -118.2, -118.1],
        number_of_dates=2,
        date="2026-03-20",
        functionality="opera_search",
        compute_cloudiness=True,
    )

    assert result == "ok"
    assert captured["cli_args"] == [
        "-b",
        "34.2",
        "34.3",
        "-118.2",
        "-118.1",
        "-n",
        "2",
        "-f",
        "opera_search",
        "-c",
        "-d",
        "2026-03-20",
    ]


def test_run_next_pass_wires_tide_flag(monkeypatch):
    captured = {}

    def fake_main(cli_args):
        captured["cli_args"] = cli_args
        return "ok"

    monkeypatch.setattr(next_pass, "main", fake_main)

    result = next_pass.run_next_pass(
        bbox=[34.2, -118.17],
        compute_tide=True,
    )

    assert result == "ok"
    assert "-t" in captured["cli_args"]
    assert captured["cli_args"] == [
        "-b",
        "34.2",
        "-118.17",
        "-n",
        "5",
        "-f",
        "both",
        "-t",
    ]


def test_find_next_overpass_routes_all_satellites(monkeypatch, tmp_path):
    sentinel_calls = []
    sleep_calls = []

    monkeypatch.setattr(next_pass.time, "sleep", lambda seconds: sleep_calls.append(seconds))

    import utils.cloudiness as cloudiness
    import utils.landsat_pass as landsat_pass
    import utils.nisar_pass as nisar_pass
    import utils.sentinel_pass as sentinel_pass
    import utils.utils as utils_mod

    monkeypatch.setattr(cloudiness, "api_limit_reached", lambda: False)
    monkeypatch.setattr(utils_mod, "bbox_type", lambda bbox: ("parsed", bbox))
    monkeypatch.setattr(
        utils_mod,
        "bbox_to_geometry",
        lambda bbox, timestamp_dir: (FakePolygon("aoi", centroid_x=10, centroid_y=20), (), FakePoint(10, 20)),
    )
    monkeypatch.setattr(
        sentinel_pass,
        "next_sentinel_pass",
        lambda sat, geometry, n_day_past, pred_cloudiness, pred_tide=False: sentinel_calls.append(
            (sat, geometry.name, n_day_past, pred_cloudiness)
        ) or {"next_collect_info": sat},
    )
    monkeypatch.setattr(
        nisar_pass,
        "next_nisar_pass",
        lambda geometry, n_day_past, arg_tide=False: {"next_collect_info": f"nisar-{geometry.name}-{n_day_past}"},
    )
    monkeypatch.setattr(
        landsat_pass,
        "next_landsat_pass",
        lambda lat, lon, geometry, n_day_past, arg_tide=False: {"next_collect_info": f"landsat-{lat}-{lon}-{n_day_past}"},
    )

    args = argparse.Namespace(
        bbox=["34.2", "-118.17"],
        sat="all",
        look_back=13,
        cloudiness=True,
    )

    result = next_pass.find_next_overpass(args, tmp_path)

    assert sentinel_calls == [
        ("sentinel1", "aoi", 13, True),
        ("sentinel2", "aoi", 13, True),
    ]
    assert sleep_calls == [60]
    assert result["nisar"]["next_collect_info"] == "nisar-aoi-13"
    assert result["landsat"]["next_collect_info"] == "landsat-20-10-13"


def test_find_next_overpass_routes_single_satellite(monkeypatch, tmp_path):
    import utils.landsat_pass as landsat_pass
    import utils.utils as utils_mod

    monkeypatch.setattr(utils_mod, "bbox_type", lambda bbox: bbox)
    monkeypatch.setattr(
        utils_mod,
        "bbox_to_geometry",
        lambda bbox, timestamp_dir: (FakePolygon("aoi", centroid_x=4, centroid_y=5), (), FakePoint(4, 5)),
    )
    monkeypatch.setattr(
        landsat_pass,
        "next_landsat_pass",
        lambda lat, lon, geometry, n_day_past, arg_tide=False: {"lat": lat, "lon": lon, "name": geometry.name, "days": n_day_past},
    )

    args = argparse.Namespace(
        bbox=["34.2", "-118.17"],
        sat="landsat",
        look_back=8,
        cloudiness=False,
    )

    result = next_pass.find_next_overpass(args, tmp_path)

    assert result["landsat"] == {"lat": 5, "lon": 4, "name": "aoi", "days": 8}
    assert result["sentinel-1"] == []
    assert result["sentinel-2"] == []
    assert result["nisar"] == []


def test_main_runs_requested_outputs_and_email(monkeypatch, tmp_path):
    sent_email = {}

    class FakeDateTime:
        @classmethod
        def now(cls):
            class _Now:
                def strftime(self, _fmt):
                    return "20260323_101010"

            return _Now()

    monkeypatch.setattr(next_pass, "datetime", FakeDateTime)
    monkeypatch.chdir(tmp_path)

    import utils.opera_products as opera_products
    import utils.plot_maps as plot_maps

    monkeypatch.setattr(next_pass, "find_next_overpass", lambda args, timestamp_dir: {
        "sentinel-1": {"next_collect_info": "s1"},
        "sentinel-2": {},
        "landsat": {},
        "nisar": {},
    })
    monkeypatch.setattr(plot_maps, "make_overpasses_map", lambda *args, **kwargs: None)
    monkeypatch.setattr(plot_maps, "make_opera_granule_map", lambda *args, **kwargs: None)
    monkeypatch.setattr(plot_maps, "make_opera_granule_drcs_map", lambda *args, **kwargs: None)
    monkeypatch.setattr(opera_products, "find_print_available_opera_products", lambda *args, **kwargs: {"x": "y"})
    monkeypatch.setattr(opera_products, "export_opera_products", lambda *args, **kwargs: None)
    monkeypatch.setattr(next_pass, "send_email", lambda subject, body, attachment=None: sent_email.update({
        "subject": subject,
        "body": body,
        "attachment": attachment,
    }))

    output_dir = next_pass.main(
        [
            "-b",
            "34.2",
            "-118.17",
            "-f",
            "both",
            "--email",
        ]
    )

    assert output_dir == Path("nextpass_outputs_20260323_101010")
    assert output_dir.exists()
    assert (output_dir / "run_output.txt").exists()
    assert "AOI: 34.2 -118.17" in sent_email["subject"]
    assert sent_email["attachment"] == output_dir / "satellite_overpasses_map.html"


def test_send_email_uses_env_password(monkeypatch):
    sent = {}

    class FakeSMTP:
        def __init__(self, user, password):
            sent["user"] = user
            sent["password"] = password

        def send(self, **kwargs):
            sent["kwargs"] = kwargs

    monkeypatch.setenv("GMAIL_APP_PSWD", "secret")
    sys.modules["yagmail"] = types.SimpleNamespace(SMTP=FakeSMTP)

    next_pass.send_email("Subject", "Body", Path("file.txt"))

    assert sent["user"] == "aria.hazards.jpl@gmail.com"
    assert sent["password"] == "secret"
    assert sent["kwargs"]["subject"] == "Subject"
