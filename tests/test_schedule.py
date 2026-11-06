from next_pass import schedule


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
