import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests
from bs4 import BeautifulSoup
from shapely.geometry import Polygon
from tabulate import tabulate

from utils.utils import (
    find_intersecting_collects,
    filter_dates_beyond_window,
    NISAR_ASCENDING_CROSSING_HOUR,
    NISAR_DESCENDING_CROSSING_HOUR,
    HOURS_PER_LONGITUDE_DEGREE,
    TIDE_PREDICTION_WINDOW_DAYS,
)
from utils.tide_prediction import (
    get_stations_in_aoi,
    get_tide_info_batch,
    make_get_tide_for_row,
)

LOGGER = logging.getLogger(__name__)

NISAR_PLAN_URL = (
    "https://assets.science.nasa.gov/content/dam/science/missions/nisar/kmz/"
    "NISAR_ROP358_TFDB_ObservationPlan_CY2026-20260305.kmz?emrc=69bc6ef442719"
)
SCRATCH_DIR = Path.cwd() / "scratch"
KMZ_FILENAME = "nisar_observation_plan.kmz"
COLLECTION_FILENAME = "nisar_collection.geojson"
KML_NS = "{http://www.opengis.net/kml/2.2}"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TRACK_FRAME_RE = re.compile(r"^T(?P<track>\d+)_F(?P<frame>\d+)$", re.IGNORECASE)


def estimate_nisar_overpass_time(date_str: str, lat: float, lon: float, pass_direction: str) -> datetime:
    """
    Estimate NISAR overpass time based on orbital specifications.

    NISAR is in a near-sun-synchronous orbit with nodal crossing times:
    - Ascending node: 6:00 AM local solar time
    - Descending node: 6:00 PM (18:00) local solar time

    This function estimates the UTC overpass time for a given location based on
    these specifications and the pass direction.

    Args:
        date_str: Date in format YYYY-MM-DD (e.g., "2026-06-28")
        lat: Latitude of the location
        lon: Longitude of the location (negative for Western hemisphere)
        pass_direction: "Ascending" or "Descending"

    Returns:
        datetime: Estimated overpass time in UTC timezone

    Notes:
        - Based on NASA specification: nodal crossing at 6 AM (asc) / 6 PM (desc) local solar time
        - Source: https://science.nasa.gov/mission/nisar/mission-overview/
        - Accuracy: ±20-40 minutes (similar to Landsat estimation)
        - Simplified calculation does not account for equation of time or orbital perturbations
        - Uses local solar time approximation: LST ≈ UTC + (lon/15) hours

    Example:
        >>> estimate_nisar_overpass_time("2026-06-28", 34.0, -118.0, "Descending")
        # Los Angeles descending: 6:00 PM local ≈ 01:52 UTC the following day
    """
    # Parse the calendar date (YYYY-MM-DD format)
    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()

    # NISAR nodal crossing times (local solar time)
    if pass_direction == "Ascending":
        local_solar_hour = NISAR_ASCENDING_CROSSING_HOUR
    elif pass_direction == "Descending":
        local_solar_hour = NISAR_DESCENDING_CROSSING_HOUR
    else:
        # Unknown direction - default to descending (most common for SAR)
        LOGGER.warning(f"Unknown pass direction '{pass_direction}', assuming Descending")
        local_solar_hour = NISAR_DESCENDING_CROSSING_HOUR

    # Local Solar Time (LST) approximation:
    # LST ≈ UTC + (longitude / HOURS_PER_LONGITUDE_DEGREE) hours
    # utc_hour may be negative or > 24; timedelta below rolls the day accordingly.
    local_solar_offset_hours = lon / HOURS_PER_LONGITUDE_DEGREE
    utc_hour = local_solar_hour - local_solar_offset_hours

    base = datetime.combine(date_obj, time.min, tzinfo=timezone.utc)
    return base + timedelta(hours=utc_hour)


def download_nisar_plan(url: str, output_path: Path) -> Path:
    """Download the NISAR observation-plan KMZ if needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        LOGGER.info("Using cached NISAR plan: %s", output_path)
        return output_path

    LOGGER.info("Downloading NISAR observation plan from NASA...")
    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        with open(output_path, "wb") as file_obj:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file_obj.write(chunk)
    return output_path


def parse_nisar_description(
    description_html: str,
) -> tuple[list[tuple[datetime, str]], dict[str, str]]:
    """Extract acquisition dates, radar modes, and key attributes."""
    soup = BeautifulSoup(description_html or "", "html.parser")
    products: list[tuple[datetime, str]] = []
    attributes: dict[str, str] = {}

    for row in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["th", "td"])]
        if len(cells) < 2:
            continue

        first, second = cells[0], cells[1]
        if DATE_RE.match(first):
            timestamp = datetime.combine(
                datetime.strptime(first, "%Y-%m-%d").date(),
                time.min,
                tzinfo=timezone.utc,
            )
            products.append((timestamp, second))
            continue

        if first in {"track", "frame", "passDirection"}:
            attributes[first] = second

    return products, attributes


def iter_nisar_placemarks(kmz_path: Path):
    """Yield parsed placemark records from the NISAR observation-plan KMZ."""
    with zipfile.ZipFile(kmz_path) as archive:
        with archive.open("doc.kml") as kml_stream:
            context = ET.iterparse(kml_stream, events=("end",))
            for _, elem in context:
                if elem.tag != f"{KML_NS}Placemark":
                    continue

                name = elem.findtext(f"{KML_NS}name", default="").strip()
                description = elem.findtext(f"{KML_NS}description", default="")
                coordinates_text = elem.findtext(
                    f".//{KML_NS}Polygon/{KML_NS}outerBoundaryIs/"
                    f"/{KML_NS}LinearRing/{KML_NS}coordinates",
                    default="",
                )
                elem.clear()

                if not coordinates_text:
                    continue

                coordinates = [
                    tuple(map(float, coord.split(",")[:2]))
                    for coord in coordinates_text.split()
                ]
                if len(coordinates) < 4:
                    continue

                products, attributes = parse_nisar_description(description)
                if not products:
                    continue

                match = TRACK_FRAME_RE.match(name)
                track = attributes.get("track")
                frame = attributes.get("frame")
                if match is not None:
                    track = track or match.group("track")
                    frame = frame or match.group("frame")

                geometry = Polygon(coordinates)
                if not geometry.is_valid:
                    geometry = geometry.buffer(0)
                if geometry.is_empty:
                    continue

                yield {
                    "name": name,
                    "track": int(track) if track else None,
                    "frame": int(frame) if frame else None,
                    "pass_direction": attributes.get("passDirection", "Unknown"),
                    "products": products,
                    "geometry": geometry,
                }


def create_nisar_collection_plan() -> Path:
    """Build or reuse a local GeoJSON collection for NISAR overpasses."""
    out_path = SCRATCH_DIR / COLLECTION_FILENAME
    if out_path.exists():
        LOGGER.info("Using cached NISAR collection: %s", out_path)
        return out_path

    kmz_path = download_nisar_plan(NISAR_PLAN_URL, SCRATCH_DIR / KMZ_FILENAME)

    rows: list[dict] = []
    for placemark in iter_nisar_placemarks(kmz_path):
        for begin_date, radar_mode in placemark["products"]:
            rows.append(
                {
                    "name": placemark["name"],
                    "track": placemark["track"],
                    "frame": placemark["frame"],
                    "pass_direction": placemark["pass_direction"],
                    "radar_mode": radar_mode,
                    "begin_date": begin_date,
                    "end_date": begin_date,
                    "geometry": placemark["geometry"],
                }
            )

    if not rows:
        LOGGER.error("No NISAR collects found in the requested date window.")
        return Path()

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    gdf = gdf.sort_values(["begin_date", "track", "frame"]).reset_index(drop=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    gdf.to_file(out_path)
    LOGGER.info("NISAR collection saved to: %s", out_path)
    return out_path


def format_collects(gdf: gpd.GeoDataFrame) -> str:
    """Format NISAR collects for CLI output."""
    gdf_sorted = gdf.sort_values("intersection_pct", ascending=False).reset_index(drop=True)
    has_tide = "tide" in gdf_sorted.columns
    table = []

    for index, row in gdf_sorted.iterrows():
        dates = row.begin_date if isinstance(row.begin_date, list) else [row.begin_date]

        # For NISAR, all dates in same track/direction have same time (local solar time)
        # Show time in Direction column, dates only in separate column (more compact)
        if dates:
            first_time = dates[0].strftime("%H:%M")
            direction_with_time = f"{row.pass_direction} (~{first_time} UTC ±20-40 min)"
            formatted_dates = [
                stamp.strftime("%Y-%m-%d")
                + (" (P)" if stamp < datetime.now(timezone.utc) else "")
                for stamp in dates
            ]
            date_lines = [
                ", ".join(formatted_dates[i:i + 5])
                for i in range(0, len(formatted_dates), 5)
            ]
            dates_str = "\n".join(date_lines)
        else:
            direction_with_time = row.pass_direction
            dates_str = "N/A"

        base_row = [
            index + 1,
            direction_with_time,
            row.track,
            row.frame,
            dates_str,
            f"{row.intersection_pct:.2f}",
        ]

        # Add tide column if present - use "nearest" station only (same as Sentinel)
        if has_tide:
            if isinstance(row.tide, list):
                tide_str = ", ".join(
                    v["nearest"] if (isinstance(v, dict) and "nearest" in v) else "N/A"
                    for v in row.tide
                )
            else:
                tide_str = row.tide["nearest"] if (isinstance(row.tide, dict) and "nearest" in row.tide) else "N/A"
            base_row.append(tide_str)

        table.append(base_row)

    headers = [
        "#",
        "Direction",
        "Track",
        "Frame",
        "Acquisition Dates (P = past)",
        "AOI % Overlap",
    ]
    if has_tide:
        headers.append("Tide in m, MLLW (HH/H/LL/L)")

    return tabulate(table, headers=headers, tablefmt="grid")


def build_collect_summaries(gdf: gpd.GeoDataFrame) -> list[str]:
    """Build per-row summaries for map popups without scraping the table."""
    summaries: list[str] = []
    has_tide = "tide" in gdf.columns

    for _, row in gdf.iterrows():
        dates = row.begin_date if isinstance(row.begin_date, list) else [row.begin_date]

        # For NISAR, all dates in same track/direction have same time
        # Show time with direction, dates only in separate section
        if dates:
            first_time = dates[0].strftime("%H:%M")
            formatted_dates = [
                stamp.strftime("%Y-%m-%d")
                + (" (P)" if stamp < datetime.now(timezone.utc) else "")
                for stamp in dates
            ]
            date_display = ", ".join(formatted_dates)
        else:
            first_time = "N/A"
            date_display = "N/A"

        lines = [
            f"Direction: {row.pass_direction} (~{first_time} UTC)",
            f"Track: {row.track}",
            f"Frame: {row.frame}",
            f"Acquisition Dates (P = past):",
            date_display,
            f"AOI % Overlap: {row.intersection_pct:.2f}",
        ]

        # Add tide information if available
        if has_tide and row.tide is not None:
            if isinstance(row.tide, list):
                tide_entries = row.tide
                if tide_entries and any(t is not None for t in tide_entries):
                    # Aggregate by station like Sentinel
                    from collections import defaultdict
                    by_station = defaultdict(list)
                    for entry in tide_entries:
                        if entry and isinstance(entry, dict) and "per_station" in entry:
                            for station_id, tide_val in entry["per_station"].items():
                                by_station[station_id].append(tide_val)

                    if by_station:
                        station_ids = list(by_station.keys())
                        prefix_len = 0
                        if len(station_ids) > 1:
                            for chars in zip(*station_ids):
                                if len(set(chars)) == 1:
                                    prefix_len += 1
                                else:
                                    break
                        tide_lines = [
                            f"  {'*' * prefix_len}{station_id[prefix_len:]}: {', '.join(vals)}"
                            for station_id, vals in by_station.items()
                        ]
                        lines.append("Tide in m, MLLW (H/L):")
                        lines.extend(tide_lines)
            elif isinstance(row.tide, dict):
                tide_str = row.tide.get("nearest", "N/A")
                lines.append(f"Tide in m, MLLW (H/L): {tide_str}")

        summaries.append("\n".join(lines))

    return summaries


def next_nisar_pass(geometry, n_day_past: float, arg_tide: bool = False) -> dict:
    """Return formatted NISAR overpasses intersecting the AOI.

    Args:
        geometry: AOI geometry (Point or Polygon)
        n_day_past: Number of days in the past to include
        arg_tide: Whether to compute NOAA tide predictions per overpass

    Returns:
        Dict with overpass information, including tide predictions if requested
    """
    try:
        collection_path = create_nisar_collection_plan()
        if not collection_path:
            raise OSError("NISAR collection could not be created.")
        gdf = gpd.read_file(collection_path)
    except (IOError, OSError, requests.RequestException, zipfile.BadZipFile) as error:
        LOGGER.error("Error reading NISAR plan file: %s", error)
        return {
            "next_collect_info": "Error reading NISAR plan file.",
            "next_collect_geometry": None,
            "intersection_pct": None,
        }

    gdf["begin_date"] = pd.to_datetime(gdf["begin_date"], utc=True, errors="coerce")
    gdf["end_date"] = pd.to_datetime(gdf["end_date"], utc=True, errors="coerce")
    gdf = gdf.dropna(subset=["begin_date", "geometry"]).reset_index(drop=True)
    n_days_earlier = datetime.now(timezone.utc) - timedelta(days=n_day_past)
    gdf = gdf.loc[gdf["begin_date"] >= n_days_earlier].reset_index(drop=True)

    collects = find_intersecting_collects(gdf, geometry)
    if collects.empty:
        last_date = gdf["end_date"].max()
        last_text = last_date.date().isoformat() if pd.notna(last_date) else "available plan"
        return {
            "next_collect_info": f"No scheduled collects before {last_text}.",
            "next_collect_geometry": None,
            "intersection_pct": None,
        }

    collects = collects.drop_duplicates(
        subset=["begin_date", "track", "frame", "pass_direction", "radar_mode"]
    )

    grouped = (
        collects.groupby(["pass_direction", "track", "frame"], dropna=False, sort=False)
        .agg(
            {
                "begin_date": lambda dates: sorted(dates),
                "radar_mode": lambda modes: list(dict.fromkeys(modes)),
                "geometry": "first",
                "intersection_pct": "max",
            }
        )
        .reset_index()
        .sort_values("intersection_pct", ascending=False)
        .reset_index(drop=True)
    )

    # NISAR neighboring frames can overlap slightly. For a given track and
    # pass direction, keep only the frame(s) with the strongest AOI overlap.
    best_overlap = grouped.groupby(
        ["pass_direction", "track"], dropna=False
    )["intersection_pct"].transform("max")
    grouped = grouped[grouped["intersection_pct"] == best_overlap].reset_index(
        drop=True
    )

    # Estimate overpass times from dates using orbit parameters
    # This replaces midnight UTC placeholders with estimated actual overpass times
    centroid = geometry.centroid
    lat, lon = centroid.y, centroid.x

    def estimate_times_for_dates(row):
        """Apply time estimation to each date in the row's begin_date list."""
        dates = row["begin_date"] if isinstance(row["begin_date"], list) else [row["begin_date"]]
        pass_direction = row["pass_direction"]

        estimated_times = []
        for dt in dates:
            # Convert datetime to date string for estimation
            date_str = dt.strftime("%Y-%m-%d")
            estimated_dt = estimate_nisar_overpass_time(date_str, lat, lon, pass_direction)
            estimated_times.append(estimated_dt)

        return estimated_times

    # Apply time estimation to all rows
    grouped["begin_date"] = grouped.apply(estimate_times_for_dates, axis=1)

    # Tide prediction (if requested)
    noaa_stations = None
    if arg_tide:
        grouped["tide"] = None
        # Get stations once for the full AOI (used for all overpasses and map display)
        try:
            noaa_stations = get_stations_in_aoi(geometry)
            if not noaa_stations:
                LOGGER.warning("No NOAA stations found in AOI - tide predictions will be empty")
        except Exception as e:
            LOGGER.warning("Could not retrieve NOAA stations for AOI: %s", e)
            noaa_stations = None

        # Track future passes for summary (initialize before noaa_stations check)
        future_passes_count = 0
        future_passes_min_date = None
        future_passes_max_date = None

        if noaa_stations:
            LOGGER.info(
                "Calculating tides for %d NISAR overpasses using %d stations ...",
                len(grouped),
                len(noaa_stations),
            )
            # Batch ALL target times across rows into a single NOAA API call
            # This avoids rate limiting (HTTP 403) from too many requests
            all_target_isos = []
            row_ranges = []  # list of (start, end) tuples in row order

            for _, row in grouped.iterrows():
                dates = row["begin_date"] if isinstance(row["begin_date"], list) else [row["begin_date"]]
                row_isos = []
                for t in dates:
                    if isinstance(t, datetime):
                        # Normalize to naive UTC string
                        if t.tzinfo is not None and t.tzinfo != timezone.utc:
                            t = t.astimezone(timezone.utc)
                        row_isos.append(t.strftime("%Y-%m-%dT%H:%M:%S"))
                    else:
                        row_isos.append(t)

                start_idx = len(all_target_isos)
                all_target_isos.extend(row_isos)
                row_ranges.append((start_idx, start_idx + len(row_isos)))

            # ONE batched call for all rows
            if all_target_isos:
                all_tide_results = get_tide_info_batch(
                    polygon=geometry,
                    target_isos=all_target_isos,
                    station_dicts=noaa_stations,
                    allow_interpolation=True,
                )
            else:
                all_tide_results = []

            # Distribute results back to each row in order
            tide_per_row = [
                all_tide_results[start:end] for start, end in row_ranges
            ]
            grouped["tide"] = tide_per_row

            # Filter dates within each row to only those within 2 months from now
            def filter_dates_and_tides(row):
                """Keep only dates and corresponding tides within 2 months."""
                nonlocal future_passes_count, future_passes_min_date, future_passes_max_date

                dates = row["begin_date"] if isinstance(row["begin_date"], list) else [row["begin_date"]]
                tides = row["tide"] if isinstance(row["tide"], list) else [row["tide"]]

                # Use shared filtering function
                (
                    filtered_dates,
                    filtered_tides,
                    count,
                    min_date,
                    max_date,
                ) = filter_dates_beyond_window(
                    dates,
                    tides,
                    max_days=TIDE_PREDICTION_WINDOW_DAYS,
                )

                # Update tracking variables
                future_passes_count += count
                if min_date is not None:
                    if future_passes_min_date is None or min_date < future_passes_min_date:
                        future_passes_min_date = min_date
                if max_date is not None:
                    if future_passes_max_date is None or max_date > future_passes_max_date:
                        future_passes_max_date = max_date

                if filtered_dates:
                    row["begin_date"] = filtered_dates
                    row["tide"] = filtered_tides
                    return row
                return None  # Drop row if no valid dates

            grouped = grouped.apply(filter_dates_and_tides, axis=1)
            grouped = grouped.dropna().reset_index(drop=True)

    table_output = format_collects(grouped)

    # Accuracy disclaimer: NISAR overpass times are estimated (not from acquisition plans)
    if not grouped.empty:
        table_output += "\n\nNote: Overpass times are estimates based on orbital specifications (±20-40 minutes accuracy)."

    # Add summary about future passes if any were filtered
    if arg_tide and future_passes_count > 0:
        table_output += f"\nNote: {future_passes_count} additional pass{'es' if future_passes_count > 1 else ''} scheduled between {future_passes_min_date.strftime('%Y-%m-%d')} and {future_passes_max_date.strftime('%Y-%m-%d')} — dates and tide predictions are not displayed for readability."

    return {
        "next_collect_info": table_output,
        "next_collect_geometry": grouped["geometry"].tolist(),
        "next_collect_summary": build_collect_summaries(grouped),
        "intersection_pct": grouped["intersection_pct"].tolist(),
        "tide": grouped["tide"].tolist() if arg_tide else None,
        "noaa_stations": noaa_stations,
    }
