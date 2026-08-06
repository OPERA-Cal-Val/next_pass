from __future__ import annotations

from datetime import datetime, timedelta, timezone

import utils.collection_builder as collection_builder
from tests.helpers import FakeFrame, FakePolygon


def test_sync_scratch_directory_deletes_obsolete_and_downloads_missing(
    monkeypatch, tmp_path
):
    logger_messages = {"info": [], "error": []}
    logger = type(
        "Logger",
        (),
        {
            "info": lambda self, message, *args: logger_messages["info"].append(
                message % args if args else message
            ),
            "error": lambda self, message, *args: logger_messages["error"].append(
                message % args if args else message
            ),
        },
    )()

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


def test_build_sentinel_collection_uses_cached_and_parsed_files(monkeypatch, tmp_path):
    logger = type(
        "Logger",
        (),
        {
            "info": lambda *args, **kwargs: None,
            "debug": lambda *args, **kwargs: None,
            "warning": lambda *args, **kwargs: None,
            "error": lambda *args, **kwargs: None,
        },
    )()

    kml_a = tmp_path / "sentinel1_alpha.kml"
    kml_b = tmp_path / "sentinel1_beta.kml"
    kml_a.write_text("a", encoding="utf-8")
    kml_b.write_text("b", encoding="utf-8")
    cached_geojson = tmp_path / "sentinel1_alpha.geojson"
    cached_geojson.write_text("{}", encoding="utf-8")

    old_date = datetime.now(timezone.utc) - timedelta(days=40)
    new_date = datetime.now(timezone.utc) - timedelta(days=2)
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
        lambda urls, mission_name, scratch_dir, logger: [kml_a, kml_b],
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
    logger = type(
        "Logger",
        (),
        {
            "info": lambda *args, **kwargs: None,
            "debug": lambda *args, **kwargs: None,
            "warning": lambda *args, **kwargs: None,
            "error": lambda *args, **kwargs: None,
        },
    )()

    monkeypatch.setattr(collection_builder, "SCRATCH_DIR", tmp_path)
    monkeypatch.setattr(
        collection_builder,
        "sync_scratch_directory",
        lambda urls, mission_name, scratch_dir, logger: [tmp_path / "broken.kml"],
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
