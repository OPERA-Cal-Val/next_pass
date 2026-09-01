from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from tests.helpers import FakeFrame, FakePolygon

import next_pass.collection_builder as collection_builder


def _make_logger():
    messages = {"info": [], "error": [], "warning": [], "debug": []}
    logger = type(
        "Logger",
        (),
        {
            level: (
                lambda self, message, *args, _level=level: messages[_level].append(
                    message % args if args else message
                )
            )
            for level in messages
        },
    )()
    return logger, messages


def test_sync_scratch_directory_deletes_obsolete_and_downloads_missing(
    monkeypatch, tmp_path
):
    logger, messages = _make_logger()

    obsolete = tmp_path / "sentinel1_old.kml"
    obsolete.write_text("old", encoding="utf-8")

    downloads = []
    monkeypatch.setattr(
        collection_builder,
        "download_kml",
        lambda url, path: downloads.append((url, path)),
    )

    local_paths = collection_builder.sync_scratch_directory(
        ["https://example.com/new.kml"],
        "sentinel1",
        tmp_path,
        logger,
    )

    assert not obsolete.exists()
    assert downloads == [
        ("https://example.com/new.kml", str(tmp_path / "sentinel1_new.kml"))
    ]
    assert local_paths == [tmp_path / "sentinel1_new.kml"]
    assert messages["error"] == []


def test_sync_scratch_directory_reports_partial_download_failure(monkeypatch, tmp_path):
    logger, messages = _make_logger()

    def fake_download(url, path):
        if "bad" in url:
            raise OSError("disk full")

    monkeypatch.setattr(collection_builder, "download_kml", fake_download)

    steps = []
    local_paths = collection_builder.sync_scratch_directory(
        ["https://example.com/good.kml", "https://example.com/bad.kml"],
        "sentinel1",
        tmp_path,
        logger,
        step_cb=steps.append,
    )

    assert local_paths == [tmp_path / "sentinel1_good.kml"]
    assert any("Failed to download" in msg for msg in messages["error"])
    assert "1 of 2 files failed to download." in messages["error"]
    assert steps == ["Downloading 1/2 files", "Downloading 2/2 files"]


def test_sync_scratch_directory_survives_unexpected_worker_error(monkeypatch, tmp_path):
    """A worker error outside the narrowed (RequestException, OSError) tuple
    must not abort the rest of the batch (thread-pool retrieval safety net)."""
    logger, messages = _make_logger()

    def fake_download(url, path):
        if "boom" in url:
            raise RuntimeError("unexpected bug")

    monkeypatch.setattr(collection_builder, "download_kml", fake_download)

    local_paths = collection_builder.sync_scratch_directory(
        ["https://example.com/ok.kml", "https://example.com/boom.kml"],
        "sentinel1",
        tmp_path,
        logger,
    )

    assert local_paths == [tmp_path / "sentinel1_ok.kml"]
    assert any("unexpected bug" in msg for msg in messages["error"])
    assert "1 of 2 files failed to download." in messages["error"]


def test_resolve_max_workers_defaults_and_bounds(monkeypatch):
    monkeypatch.delenv("NEXTPASS_MAX_WORKERS", raising=False)
    assert collection_builder._resolve_max_workers(20) == 8
    assert collection_builder._resolve_max_workers(3) == 3


def test_resolve_max_workers_honors_env_override(monkeypatch):
    monkeypatch.setenv("NEXTPASS_MAX_WORKERS", "2")
    assert collection_builder._resolve_max_workers(10) == 2
    assert collection_builder._resolve_max_workers(1) == 1


def test_resolve_max_workers_falls_back_on_bad_value(monkeypatch):
    monkeypatch.setenv("NEXTPASS_MAX_WORKERS", "not-a-number")
    assert collection_builder._resolve_max_workers(20) == 8

    monkeypatch.setenv("NEXTPASS_MAX_WORKERS", "0")
    assert collection_builder._resolve_max_workers(20) == 8


def test_resolve_platform_direct_match():
    platform_by_name = {"alpha": "S1A"}
    result = collection_builder.resolve_platform(
        Path("sentinel1_alpha.kml"), platform_by_name
    )
    assert result == "S1A"


def test_resolve_platform_drops_leading_token():
    # kml stem is "sentinel1_s1c_2026active"; direct match fails, but dropping
    # the leading "sentinel1_" token matches the URL-derived key.
    platform_by_name = {"s1c_2026active": "S1C"}
    result = collection_builder.resolve_platform(
        Path("sentinel1_s1c_2026active.kml"), platform_by_name
    )
    assert result == "S1C"


def test_resolve_platform_falls_back_to_substring_match():
    platform_by_name = {"s1c": "S1C"}
    result = collection_builder.resolve_platform(
        Path("sentinel1_random_s1c_suffix.kml"), platform_by_name
    )
    assert result == "S1C"


def test_resolve_platform_returns_none_when_no_match():
    assert (
        collection_builder.resolve_platform(Path("unrelated.kml"), {"other": "X"})
        is None
    )
    assert collection_builder.resolve_platform(Path("unrelated.kml"), {}) is None


def test_build_sentinel_collection_uses_cached_and_parsed_files(monkeypatch, tmp_path):
    logger, _ = _make_logger()

    kml_a = tmp_path / "sentinel1_alpha.kml"
    kml_b = tmp_path / "sentinel1_beta.kml"
    kml_a.write_text("a", encoding="utf-8")
    kml_b.write_text("b", encoding="utf-8")
    cached_geojson = tmp_path / "sentinel1_alpha.geojson"
    cached_geojson.write_text("{}", encoding="utf-8")

    old_date = datetime.now(UTC) - timedelta(days=40)
    new_date = datetime.now(UTC) - timedelta(days=2)
    cached_frame = FakeFrame(
        [{"begin_date": old_date, "geometry": FakePolygon("cached")}]
    )
    parsed_frame = FakeFrame(
        [{"begin_date": new_date, "geometry": FakePolygon("fresh")}]
    )

    monkeypatch.setattr(collection_builder, "SCRATCH_DIR", tmp_path)
    monkeypatch.setattr(
        collection_builder,
        "sync_scratch_directory",
        lambda urls, mission_name, scratch_dir, logger, step_cb=None: [kml_a, kml_b],
    )
    monkeypatch.setattr(collection_builder.gpd, "read_file", lambda path: cached_frame)
    monkeypatch.setattr(collection_builder, "parse_kml", lambda path: parsed_frame)
    monkeypatch.setattr(
        collection_builder.pd,
        "concat",
        lambda frames: FakeFrame([row for frame in frames for row in frame.rows]),
    )
    monkeypatch.setattr(
        collection_builder.pd, "to_datetime", lambda values, utc=True: values
    )

    output = collection_builder.build_sentinel_collection(
        urls=["https://example.com/alpha.kml", "https://example.com/beta.kml"],
        n_day_past=13,
        mission_name="sentinel1",
        out_filename="out.geojson",
        logger=logger,
        platforms=["S1A", "S1C"],
    )

    assert output == tmp_path / "out.geojson"
    assert output.exists()


def test_build_sentinel_collection_returns_empty_path_when_no_frames(
    monkeypatch, tmp_path
):
    logger, _ = _make_logger()

    monkeypatch.setattr(collection_builder, "SCRATCH_DIR", tmp_path)
    monkeypatch.setattr(
        collection_builder,
        "sync_scratch_directory",
        lambda urls, mission_name, scratch_dir, logger, step_cb=None: [
            tmp_path / "broken.kml"
        ],
    )
    monkeypatch.setattr(
        collection_builder,
        "parse_kml",
        lambda path: (_ for _ in ()).throw(ValueError("bad kml")),
    )

    output = collection_builder.build_sentinel_collection(
        urls=["https://example.com/broken.kml"],
        n_day_past=13,
        mission_name="sentinel1",
        out_filename="out.geojson",
        logger=logger,
    )

    assert output == collection_builder.Path()


def test_build_sentinel_collection_reports_partial_parse_failure(monkeypatch, tmp_path):
    logger, messages = _make_logger()

    kml_ok = tmp_path / "sentinel1_ok.kml"
    kml_bad = tmp_path / "sentinel1_bad.kml"
    kml_ok.write_text("ok", encoding="utf-8")
    kml_bad.write_text("bad", encoding="utf-8")

    new_date = datetime.now(UTC) - timedelta(days=2)
    parsed_frame = FakeFrame(
        [{"begin_date": new_date, "geometry": FakePolygon("fresh")}]
    )

    monkeypatch.setattr(collection_builder, "SCRATCH_DIR", tmp_path)
    monkeypatch.setattr(
        collection_builder,
        "sync_scratch_directory",
        lambda urls, mission_name, scratch_dir, logger, step_cb=None: [
            kml_ok,
            kml_bad,
        ],
    )

    def fake_parse_kml(path):
        if path == kml_bad:
            raise ValueError("malformed kml")
        return parsed_frame

    monkeypatch.setattr(collection_builder, "parse_kml", fake_parse_kml)
    monkeypatch.setattr(
        collection_builder.pd,
        "concat",
        lambda frames: FakeFrame([row for frame in frames for row in frame.rows]),
    )
    monkeypatch.setattr(
        collection_builder.pd, "to_datetime", lambda values, utc=True: values
    )

    output = collection_builder.build_sentinel_collection(
        urls=["https://example.com/ok.kml", "https://example.com/bad.kml"],
        n_day_past=13,
        mission_name="sentinel1",
        out_filename="out.geojson",
        logger=logger,
    )

    assert output == tmp_path / "out.geojson"
    assert any("Failed to parse" in msg for msg in messages["error"])
    assert "1 of 2 files failed to parse." in messages["error"]


def test_build_sentinel_collection_survives_unexpected_parse_worker_error(
    monkeypatch, tmp_path
):
    """A worker error outside the narrowed exception tuple must not abort the
    rest of the batch (thread-pool retrieval safety net)."""
    logger, messages = _make_logger()

    kml_ok = tmp_path / "sentinel1_ok.kml"
    kml_boom = tmp_path / "sentinel1_boom.kml"
    kml_ok.write_text("ok", encoding="utf-8")
    kml_boom.write_text("boom", encoding="utf-8")

    new_date = datetime.now(UTC) - timedelta(days=2)
    parsed_frame = FakeFrame(
        [{"begin_date": new_date, "geometry": FakePolygon("fresh")}]
    )

    monkeypatch.setattr(collection_builder, "SCRATCH_DIR", tmp_path)
    monkeypatch.setattr(
        collection_builder,
        "sync_scratch_directory",
        lambda urls, mission_name, scratch_dir, logger, step_cb=None: [
            kml_ok,
            kml_boom,
        ],
    )

    def fake_parse_kml(path):
        if path == kml_boom:
            raise RuntimeError("unexpected bug")
        return parsed_frame

    monkeypatch.setattr(collection_builder, "parse_kml", fake_parse_kml)
    monkeypatch.setattr(
        collection_builder.pd,
        "concat",
        lambda frames: FakeFrame([row for frame in frames for row in frame.rows]),
    )
    monkeypatch.setattr(
        collection_builder.pd, "to_datetime", lambda values, utc=True: values
    )

    output = collection_builder.build_sentinel_collection(
        urls=["https://example.com/ok.kml", "https://example.com/boom.kml"],
        n_day_past=13,
        mission_name="sentinel1",
        out_filename="out.geojson",
        logger=logger,
    )

    assert output == tmp_path / "out.geojson"
    assert any("unexpected bug" in msg for msg in messages["error"])
    assert "1 of 2 files failed to parse." in messages["error"]
