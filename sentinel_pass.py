import logging
from pathlib import Path

import geopandas as gpd
from tabulate import tabulate

from collection_builder import build_sentinel_collection
from utils import find_intersecting_collects, scrape_esa_download_urls

LOGGER = logging.getLogger("sentinel_pass")

SENT1_URL = (
    "https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-1/acquisition-plans"
)
SENT2_URL = (
    "https://sentinels.copernicus.eu/web/sentinel/copernicus/sentinel-2/acquisition-plans"
)


def create_s1_collection_plan() -> Path:
    """Prepare Sentinel-1 acquisition plan collection."""
    urls = scrape_esa_download_urls(SENT1_URL, "sentinel-1a")
    return build_sentinel_collection(
        urls, "sentinel1", "sentinel_1_collection.geojson", LOGGER
    )


def create_s2_collection_plan() -> Path:
    """Prepare Sentinel-2 acquisition plan collection."""
    urls = scrape_esa_download_urls(SENT2_URL, "sentinel-2a")
    urls += scrape_esa_download_urls(SENT2_URL, "sentinel-2b")
    return build_sentinel_collection(
        urls, "sentinel2", "sentinel_2_collection.geojson", LOGGER
    )


def format_collects(gdf: gpd.GeoDataFrame) -> str:
    """Format Sentinel collects into a tabulated string."""
    table = [
        (idx + 1,
         row.begin_date.strftime("%Y-%m-%d %H:%M:%S"),
         row.orbit_relative,
         f"{row.intersection_pct:.2f}")
        for idx, row in gdf.iterrows()
    ]
    headers = ["#", "Collection Date & Time", "Relative Orbit", "AOI % Overlap"]
    return tabulate(table, headers, tablefmt="grid")


def next_sentinel_pass(create_plan_func, geometry) -> dict:
    """
    Load Sentinel collection, find intersects, and format results.

    Args:
        create_plan_func: Function to create or fetch collection file.
        geometry: Shapely geometry (Point or Polygon) to check intersects.

    Returns:
        dict: Dictionary with formatted collect info, collect geometries, and percentage overlap of each collect with the input geometry (AOI).
    """
    try:
        gdf = gpd.read_file(create_plan_func())
    except (IOError, OSError) as e:
        LOGGER.error(f"Error reading Sentinel plan file: {e}")
        return {
            "next_collect_info": "Error reading plan file.",
            "next_collect_geometry": None,
            "intersection_pct": None,
        }

    collects = find_intersecting_collects(gdf, geometry)
    collects = collects.drop_duplicates(subset=["begin_date", "orbit_relative"])
    if not collects.empty:
        return {
            "next_collect_info": format_collects(collects),
            "next_collect_geometry": collects.geometry.tolist(),
            "intersection_pct": collects['intersection_pct'].tolist(),
        }
    else:
        return {
            "next_collect_info": f"No scheduled collects before {gdf['end_date'].max().date()}.",
            "intersection_pct": None,
        }
