import logging
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from tabulate import tabulate

from utils.cloudiness import make_get_cloudiness_for_row
from utils.tide_prediction import (
    make_get_tide_for_row,
    get_stations_in_aoi,
    get_tide_info_batch,
)
from utils.collection_builder import build_sentinel_collection
from utils.utils import find_intersecting_collects, scrape_esa_download_urls

LOGGER = logging.getLogger("sentinel_pass")

SENT1_URL = "https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans"
SENT2_URL = "https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-2/acquisition-plans"


def format_date_lines(dates: list[datetime], per_line: int = 5) -> str:
    """Wrap Sentinel acquisition dates across multiple lines."""
    formatted_dates = [
        d.strftime("%Y-%m-%d %H:%M:%S")
        + (" (P)" if d < datetime.now(timezone.utc) else "")
        for d in dates
    ]
    return "\n".join(
        ", ".join(formatted_dates[i:i + per_line])
        for i in range(0, len(formatted_dates), per_line)
    )


def build_collect_summaries(gdf: gpd.GeoDataFrame) -> list[str]:
    """Build per-row summaries for map popups without scraping the table."""
    summaries: list[str] = []
    has_platform = (
        "platform" in gdf.columns
        and gdf["platform"].notnull().any()
        and (gdf["platform"].astype(str) != "").any()
    )
    has_cloudiness = "cloudiness" in gdf.columns
    has_tide = "tide" in gdf.columns

    for _, row in gdf.iterrows():
        parts = []
        if has_platform:
            parts.append(f"Platform: {row.platform}")
        parts.append(f"Relative Orbit: {row.orbit_relative}")
        parts.append(f"Collection Date & UTC Time (P = past):\n{format_date_lines(row.begin_date)}")
        parts.append(f"AOI % Overlap: {row.intersection_pct:.2f}")

        if has_cloudiness:
            if isinstance(row.cloudiness, list):
                cloud_str = ", ".join(
                    f"{v:.2f}" if v is not None else "N/A" for v in row.cloudiness
                )
            else:
                cloud_str = f"{row.cloudiness:.2f}"
            parts.append(f"Cloudiness (%): {cloud_str}")

        if has_tide:
            tide_entries = row.tide if isinstance(row.tide, list) else [row.tide]
            by_station: dict = {}
            for entry in tide_entries:
                if isinstance(entry, dict) and "per_station" in entry:
                    for sid, val in entry["per_station"].items():
                        by_station.setdefault(sid, []).append(val)
            if by_station:
                station_ids = list(by_station.keys())
                prefix_len = 0
                if len(station_ids) > 1:
                    for chars in zip(*station_ids):
                        if len(set(chars)) == 1:
                            prefix_len += 1
                        else:
                            break
                tide_lines = "\n".join(
                    f"{'*' * prefix_len}{sid[prefix_len:]}: {', '.join(vals)}"
                    for sid, vals in by_station.items()
                )
            else:
                tide_lines = "N/A"
            parts.append(f"Tide in m, MLLW (High/Low):\n{tide_lines}")

        summaries.append("\n".join(parts))

    return summaries


def create_s1_collection_plan(n_day_past: float) -> Path:
    """Prepare Sentinel-1 acquisition plan collection."""
    urls_a = scrape_esa_download_urls(SENT1_URL, "sentinel-1a")
    urls_c = scrape_esa_download_urls(SENT1_URL, "sentinel-1c")
    urls_d = scrape_esa_download_urls(SENT1_URL, "sentinel-1d")
    urls = urls_a + urls_c + urls_d

    platforms = ["S1A"] * len(urls_a) + ["S1C"] * len(urls_c) + ["S1D"] * len(urls_d)

    return build_sentinel_collection(
        urls,
        n_day_past,
        "sentinel1",
        "sentinel_1_collection.geojson",
        LOGGER,
        platforms,
    )


def create_s2_collection_plan(n_day_past: float) -> Path:
    """Prepare Sentinel-2 acquisition plan collection."""
    urls_a = scrape_esa_download_urls(SENT2_URL, "sentinel-2a")
    urls_b = scrape_esa_download_urls(SENT2_URL, "sentinel-2b")
    urls_c = scrape_esa_download_urls(SENT2_URL, "sentinel-2c")
    urls = urls_a + urls_b + urls_c

    platforms = (
        ["S2A"] * len(urls_a) + ["S2B"] * len(urls_b) + ["S2C"] * len(urls_c)
    )

    return build_sentinel_collection(
        urls,
        n_day_past,
        "sentinel2",
        "sentinel_2_collection.geojson",
        LOGGER,
        platforms,
    )


def format_collects(gdf: gpd.GeoDataFrame) -> str:
    """Format a collects GeoDataFrame into a tabulated string."""
    gdf_sorted = gdf.sort_values("intersection_pct", ascending=False)

    has_cloudiness = "cloudiness" in gdf_sorted.columns
    has_tide = "tide" in gdf_sorted.columns

    # Only show platform column if it has at least one non-empty value
    has_platform = (
        "platform" in gdf_sorted.columns
        and gdf_sorted["platform"].notnull().any()
        and (gdf_sorted["platform"].astype(str) != "").any()
    )

    table = []

    for i, row in gdf_sorted.iterrows():
        base_row = [i + 1]  # Row number

        if has_platform:
            base_row.append(row.platform)

        # Relative orbit
        base_row.append(row.orbit_relative)

        # Dates
        dates_str = format_date_lines(row.begin_date)
        base_row.append(dates_str)

        # Intersection %
        base_row.append(f"{row.intersection_pct:.2f}")

        if has_cloudiness:
            if isinstance(row.cloudiness, list):
                cloud_str = ", ".join(
                    f"{v:.2f}" if v is not None else "N/A" for v in row.cloudiness
                )
            else:
                cloud_str = f"{row.cloudiness:.2f}"
            base_row.append(cloud_str)

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

    headers = ["#"]
    if has_platform:
        headers.append("Platform")
    headers += [
        "Relative Orbit",
        "Collection Date & UTC Time (P = past)",
        "AOI % Overlap",
    ]
    if has_cloudiness:
        headers.append("Cloudiness (%)")
    if has_tide:
        headers.append("Tide in m, MLLW (High/Low)")
    return tabulate(table, headers=headers, tablefmt="grid")


def unique_geometry_per_orbit(collects: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Aggregate granules per orbit, keeping unique geometries and
    separating S1A, S1C and S1D even if they share the same orbit.
    """

    def first_unique_geoms(geoms):
        """Keep only unique geometries based on WKT."""
        seen = set()
        unique = []
        for g in geoms:
            wkt = g.wkt
            if wkt not in seen:
                seen.add(wkt)
                unique.append(g)
        return unique

    has_cloudiness = "cloudiness" in collects.columns

    # Ensure begin_date is datetime
    collects["begin_date"] = pd.to_datetime(
        collects["begin_date"], format="ISO8601", errors="raise"
    )

    # Aggregation dictionary
    agg_dict: dict = {
        "begin_date": lambda dates: sorted(dates),
        "geometry": first_unique_geoms,
        "intersection_pct": "first",
    }

    if has_cloudiness:
        agg_dict["cloudiness"] = "first"

    # Group by both orbit_relative and platform for Sentinel-1
    groupby_cols = ["orbit_relative"]
    if "platform" in collects.columns and collects["platform"].notna().any():
        groupby_cols.append("platform")

    grouped = collects.groupby(groupby_cols).agg(agg_dict).reset_index()

    # Flatten geometry list to first geometry only
    grouped["geometry"] = grouped["geometry"].apply(
        lambda geoms: geoms[0] if geoms else None
    )

    # Sort by intersection percentage
    grouped = grouped.sort_values("intersection_pct", ascending=False
                                  ).reset_index(
        drop=True
    )

    return grouped


def next_sentinel_pass(
    sat: str,
    geometry,
    n_day_past: float,
    arg_cloudiness: bool,
    arg_tide: bool = False,
) -> dict:
    """
    Load Sentinel collection, find intersects, and format results.

    Args:
        sat: "sentinel1" or "sentinel2".
        geometry: Shapely geometry (Point or Polygon) to check intersects.
        n_day_past: How many days back to include in collection.
        arg_cloudiness: Whether to compute cloudiness per overpass.
        arg_tide: Whether to compute NOAA tide predictions per overpass.

    Returns:
        dict: Dictionary with formatted collect info, collect geometries,
        and percentage overlap of each collect with the input geometry (AOI).
    """
    try:
        if sat == "sentinel1":
            gdf = gpd.read_file(create_s1_collection_plan(n_day_past))
        elif sat == "sentinel2":
            gdf = gpd.read_file(create_s2_collection_plan(n_day_past))
        else:
            LOGGER.error("Unsupported satellite identifier: %s", sat)
            return {
                "next_collect_info": "Unsupported satellite identifier.",
                "next_collect_geometry": None,
                "intersection_pct": None,
            }
    except (IOError, OSError) as e:
        LOGGER.error("Error reading Sentinel plan file: %s", e)
        return {
            "next_collect_info": "Error reading plan file.",
            "next_collect_geometry": None,
            "intersection_pct": None,
        }

    if "platform" not in gdf.columns:
        LOGGER.warning(
            "The collection plan does not contain a 'platform' column.")

    collects = find_intersecting_collects(gdf, geometry)
    dedupe_cols = ["begin_date", "orbit_relative"]
    if "platform" in collects.columns:
        dedupe_cols.append("platform")
    collects = collects.drop_duplicates(subset=dedupe_cols)

    if "platform" not in gdf.columns:
        LOGGER.warning(
            "The collection plan does not contain a 'platform' column.")

    if not collects.empty:
        groupby_cols = ["orbit_relative"]
        if "platform" in collects.columns and collects["platform"
                                                       ].notna().any():
            groupby_cols.append("platform")

        # Group collects by orbit, aggregate timestamps as list
        collects_grouped = (
            collects.groupby(groupby_cols, sort=False)
            .agg(
                {
                    "begin_date": list,
                    "geometry": "first",
                    "intersection_pct": "first",
                }
            )
            .reset_index()
        )
        num_rows = len(collects_grouped)
        # cloudiness
        if arg_cloudiness:
            collects_grouped["cloudiness"] = None
            LOGGER.info(
                "Calculating cloudiness for %d overpasses ...",
                num_rows,
            )
            get_cloudiness_for_row = make_get_cloudiness_for_row(geometry)
            collects_grouped["cloudiness"] = collects_grouped.apply(
                get_cloudiness_for_row,
                axis=1,
            )
        # tide prediction
        noaa_stations = None
        if arg_tide:
            collects_grouped["tide"] = None
            # Get stations once for the full AOI (used for all overpasses and map display)
            try:
                noaa_stations = get_stations_in_aoi(geometry)
                if not noaa_stations:
                    LOGGER.warning("No NOAA stations found in AOI - tide predictions will be empty")
            except Exception as e:
                LOGGER.warning("Could not retrieve NOAA stations for AOI: %s", e)
                noaa_stations = None

            if noaa_stations:
                LOGGER.info(
                    "Calculating tides for %d overpasses using %d stations ...",
                    num_rows,
                    len(noaa_stations),
                )
                # Batch ALL target times across rows into a single NOAA API call
                # This avoids rate limiting (HTTP 403) from too many requests
                all_target_isos = []
                row_ranges = []  # list of (start_idx, end_idx) tuples in row order

                for _, row in collects_grouped.iterrows():
                    dates = row["begin_date"] if isinstance(row["begin_date"], list) else [row["begin_date"]]
                    row_isos = []
                    for t in dates:
                        if isinstance(t, datetime):
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
                collects_grouped["tide"] = tide_per_row

        return {
            "next_collect_info": format_collects(collects_grouped),
            "next_collect_geometry": collects_grouped["geometry"].tolist(),
            "next_collect_summary": build_collect_summaries(collects_grouped),
            "intersection_pct": collects_grouped["intersection_pct"].tolist(),
            "cloudiness": collects_grouped["cloudiness"].tolist(
            ) if arg_cloudiness else None,
            "tide": collects_grouped["tide"].tolist() if arg_tide else None,
            "noaa_stations": noaa_stations,
        }

    if collects.empty:
        end_date_msg = ""
        if "end_date" in gdf.columns and not gdf.empty:
            try:
                max_date = gdf["end_date"].max()
                end_date_msg = f" before {max_date.strftime('%Y-%m-%d')}"
            except Exception:
                pass
        return {
            "next_collect_info": f"No scheduled collects{end_date_msg}.",
            "intersection_pct": None,
            "cloudiness": None,
            "tide": None,
            "noaa_stations": None,
        }
