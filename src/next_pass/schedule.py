import argparse
import json
import logging
import shutil
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from next_pass.landsat_pass import (
    DATE_FORMAT,
    LANDSAT_MISSIONS,
    MAP_SERVICE_URL,
    _cycle_for_date,
    estimate_landsat_overpass_time,
    load_landsat_schedule_source,
)
from next_pass.nisar_pass import (
    create_nisar_collection_plan,
    estimate_nisar_overpass_time,
)
from next_pass.sentinel_pass import (
    create_s1_collection_plan,
    create_s2_collection_plan,
)
from next_pass.utils import arcgis_to_polygon

LOGGER = logging.getLogger("next_pass.schedule")

SCHEDULE_COLUMNS = [
    "sensor",
    "platform",
    "mode",
    "orbit_direction",
    "track",
    "begin_time",
    "end_time",
    "time_is_estimated",
    "geometry",
]
ALL_SENSORS = ["sentinel-1", "sentinel-2", "landsat", "nisar"]
WRS2_PAGE_SIZE = 2000
LANDSAT_SIMPLIFY_DEG = 0.01


def _empty_schedule() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {col: [] for col in SCHEDULE_COLUMNS}, geometry="geometry", crs="EPSG:4326"
    )


def _window(days: int) -> tuple[datetime, datetime]:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=days)


def build_sentinel_schedule(sensor: str, days: int) -> gpd.GeoDataFrame:
    builders = {
        "sentinel-1": create_s1_collection_plan,
        "sentinel-2": create_s2_collection_plan,
    }
    collection_path = builders[sensor](n_day_past=0)
    gdf = gpd.read_file(collection_path)
    if gdf.empty:
        return _empty_schedule()

    start, end = _window(days)
    begin = pd.to_datetime(gdf["begin_date"], utc=True, format="mixed")
    finish = pd.to_datetime(gdf["end_date"], utc=True, format="mixed")
    keep = (begin >= start) & (begin < end)

    out = gpd.GeoDataFrame(
        {
            "sensor": sensor,
            "platform": gdf["platform"],
            "mode": gdf["mode"].astype(str),
            "orbit_direction": "",
            "track": pd.to_numeric(gdf["orbit_relative"], errors="coerce"),
            "begin_time": begin,
            "end_time": finish,
            "time_is_estimated": False,
            "geometry": gdf.geometry,
        },
        crs="EPSG:4326",
    )
    return out[keep].reset_index(drop=True)


def build_nisar_schedule(days: int) -> gpd.GeoDataFrame:
    collection_path = create_nisar_collection_plan()
    gdf = gpd.read_file(collection_path)
    if gdf.empty:
        return _empty_schedule()

    start, end = _window(days)
    begin = pd.to_datetime(gdf["begin_date"], utc=True, format="mixed")
    gdf = gdf[(begin >= start) & (begin < end)].reset_index(drop=True)
    if gdf.empty:
        return _empty_schedule()

    centroids = gdf.geometry.centroid
    times = [
        estimate_nisar_overpass_time(
            pd.Timestamp(b).strftime("%Y-%m-%d"), pt.y, pt.x, direction
        )
        for b, pt, direction in zip(
            pd.to_datetime(gdf["begin_date"], utc=True, format="mixed"),
            centroids,
            gdf["pass_direction"],
        )
    ]

    return gpd.GeoDataFrame(
        {
            "sensor": "nisar",
            "platform": "NISAR",
            "mode": gdf["radar_mode"].astype(str),
            "orbit_direction": gdf["pass_direction"].str.lower(),
            "track": pd.to_numeric(gdf["track"], errors="coerce"),
            "begin_time": pd.to_datetime(times, utc=True),
            "end_time": pd.to_datetime(times, utc=True),
            "time_is_estimated": True,
            "geometry": gdf.geometry,
        },
        crs="EPSG:4326",
    )


def fetch_wrs2_outlines(session: requests.Session) -> gpd.GeoDataFrame:
    records = []
    offset = 0
    while True:
        params = {
            "where": "1=1",
            "outFields": "PATH,ROW,MODE",
            "returnGeometry": "true",
            "outSR": 4326,
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": WRS2_PAGE_SIZE,
        }
        response = session.get(f"{MAP_SERVICE_URL}query", params=params, timeout=60)
        response.raise_for_status()
        payload = response.json()
        features = payload.get("features", [])
        records.extend(features)
        LOGGER.info("Fetched %s WRS-2 outlines...", len(records))
        if not payload.get("exceededTransferLimit") or not features:
            break
        offset += len(features)

    rows = [
        {
            "path": int(f["attributes"]["PATH"]),
            "row": int(f["attributes"]["ROW"]),
            "direction": "ascending"
            if f["attributes"]["MODE"] == "A"
            else "descending",
            "geometry": arcgis_to_polygon(f.get("geometry")),
        }
        for f in records
    ]
    gdf = gpd.GeoDataFrame(
        [r for r in rows if r["geometry"] is not None], crs="EPSG:4326"
    )
    return gdf


def build_landsat_schedule(
    days: int,
    session: requests.Session | None = None,
    wrs2: gpd.GeoDataFrame | None = None,
) -> gpd.GeoDataFrame:
    session = session or requests.Session()
    schedule_source = load_landsat_schedule_source(session)
    if schedule_source.source != "modern":
        LOGGER.warning(
            "Landsat schedule unavailable via modern USGS source; skipping "
            "(warnings: %s)",
            schedule_source.warnings,
        )
        return _empty_schedule()

    wrs2 = wrs2 if wrs2 is not None else fetch_wrs2_outlines(session)
    start, _ = _window(days)

    frames = []
    for offset in range(days):
        day = (start + timedelta(days=offset)).date()
        cycle = _cycle_for_date(day, schedule_source.cycle_sequence)
        for mission in LANDSAT_MISSIONS:
            paths = schedule_source.mission_cycle_paths.get(mission, {}).get(
                cycle, set()
            )
            active = wrs2[wrs2["path"].isin(paths)]
            frames.extend(
                _landsat_swath_row(mission, day, path, direction, group)
                for (path, direction), group in active.groupby(["path", "direction"])
            )

    if not frames:
        return _empty_schedule()
    return gpd.GeoDataFrame(frames, crs="EPSG:4326")


def _landsat_swath_row(
    mission: str, day: date, path: int, direction: str, group: gpd.GeoDataFrame
) -> dict:
    geometry = group.geometry.union_all().simplify(LANDSAT_SIMPLIFY_DEG)
    centroid = geometry.centroid
    overpass = estimate_landsat_overpass_time(
        day.strftime(DATE_FORMAT), centroid.y, centroid.x
    )
    return {
        "sensor": mission.replace("_", "-"),
        "platform": mission.replace("landsat_", "L"),
        "mode": "OLI/TIRS",
        "orbit_direction": direction,
        "track": path,
        "begin_time": overpass,
        "end_time": overpass,
        "time_is_estimated": True,
        "geometry": geometry,
    }


def build_schedule(sensors: list[str], days: int) -> gpd.GeoDataFrame:
    parts = []
    if "sentinel-1" in sensors:
        LOGGER.info("Building Sentinel-1 schedule...")
        parts.append(build_sentinel_schedule("sentinel-1", days))
    if "sentinel-2" in sensors:
        LOGGER.info("Building Sentinel-2 schedule...")
        parts.append(build_sentinel_schedule("sentinel-2", days))
    if "landsat" in sensors:
        LOGGER.info("Building Landsat schedule...")
        parts.append(build_landsat_schedule(days))
    if "nisar" in sensors:
        LOGGER.info("Building NISAR schedule...")
        parts.append(build_nisar_schedule(days))

    combined = pd.concat(parts, ignore_index=True) if parts else _empty_schedule()
    combined["begin_time"] = pd.to_datetime(combined["begin_time"], utc=True)
    combined["end_time"] = pd.to_datetime(combined["end_time"], utc=True)
    return gpd.GeoDataFrame(combined, geometry="geometry", crs="EPSG:4326").sort_values(
        "begin_time", ignore_index=True
    )


def write_schedule(gdf: gpd.GeoDataFrame, output: Path, days: int) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_parquet(output, index=False)
    sensor_counts = gdf["sensor"].value_counts().to_dict() if len(gdf) else {}
    meta = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "days": days,
        "n_passes": int(len(gdf)),
        "sensors": {k: int(v) for k, v in sensor_counts.items()},
    }
    output.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2))
    return output


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Precompute upcoming satellite passes into a GeoParquet file."
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("upcoming_passes.parquet"),
        help="Output GeoParquet path (a .meta.json sidecar is written next to it).",
    )
    parser.add_argument(
        "-d",
        "--days",
        type=int,
        default=7,
        help="Number of days ahead to include (default: 7).",
    )
    parser.add_argument(
        "-s",
        "--sensors",
        nargs="+",
        default=ALL_SENSORS,
        choices=ALL_SENSORS,
        help="Sensors to include. Default is all.",
    )
    parser.add_argument(
        "--no-refresh",
        action="store_true",
        help="Reuse cached files in ./scratch instead of re-downloading plans.",
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
    )
    return parser


def main(cli_args: list[str] | None = None) -> Path:
    args = create_parser().parse_args(cli_args)
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    scratch = Path.cwd() / "scratch"
    if not args.no_refresh and scratch.exists():
        LOGGER.info("Clearing %s for a fresh build.", scratch)
        shutil.rmtree(scratch)

    gdf = build_schedule(args.sensors, args.days)
    output = write_schedule(gdf, args.output, args.days)
    LOGGER.info("Wrote %s passes to %s", len(gdf), output)
    return output


def cli() -> None:
    main()


if __name__ == "__main__":
    main()
