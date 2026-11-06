import json

from next_pass import schedule
from next_pass.landsat_pass import LandsatScheduleSource


def test_build_landsat_schedule_marks_source_failure(monkeypatch):
    monkeypatch.setattr(
        schedule,
        "load_landsat_schedule_source",
        lambda session: LandsatScheduleSource(
            source="unavailable",
            warnings=["source offline"],
        ),
    )

    result = schedule.build_landsat_schedule(days=7)

    assert result.empty
    assert result.attrs["source_status"] == "failed"


def test_build_landsat_schedule_marks_successful_empty_result(monkeypatch):
    monkeypatch.setattr(
        schedule,
        "load_landsat_schedule_source",
        lambda session: LandsatScheduleSource(
            source="modern",
            cycle_sequence=list(range(1, 17)),
            mission_cycle_paths={
                mission: {1: {101}} for mission in schedule.LANDSAT_MISSIONS
            },
        ),
    )
    wrs2 = schedule.gpd.GeoDataFrame(
        {"path": [], "direction": [], "geometry": []},
        geometry="geometry",
        crs="EPSG:4326",
    )

    result = schedule.build_landsat_schedule(days=1, wrs2=wrs2)

    assert result.empty
    assert result.attrs["source_status"] == "ok"


def test_write_schedule_includes_source_status(tmp_path):
    result = schedule._empty_schedule()
    result.attrs["source_status"] = {"landsat": "failed", "nisar": "ok"}
    output = tmp_path / "schedule.parquet"

    schedule.write_schedule(result, output, days=7)

    metadata = json.loads(output.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert metadata["sources"] == {"landsat": "failed", "nisar": "ok"}
