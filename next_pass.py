#! /usr/bin/env python3
import argparse
import logging
from datetime import datetime
from typing import Tuple, Union, Optional

import geopandas as gpd
from shapely.geometry import Point, Polygon, shape
import asf_search as asf

from s1_collection import create_s1_collection_plan
from s2_collection import create_s2_collection_plan
from landsat_pass import next_landsat_pass
from tabulate import tabulate

LOGGER = logging.getLogger("next_pass")

EXAMPLE = """Example usage:
    next_pass.py --latitude 34.615 --longitude -81.936 --satellite sentinel-1
"""

def format_collection_outputs(next_collect, next_collect_orbit):
    table_data = [[i + 1, dt.strftime('%Y-%m-%d %H:%M:%S'), orbit] 
                  for i, (dt, orbit) in enumerate(zip(next_collect, next_collect_orbit))]
    return tabulate(table_data, headers=["#", "Collection Date & Time", "Relative Orbit"], tablefmt="grid")


def create_parser() -> argparse.ArgumentParser:
    """Create parser for command line arguments."""
    parser = argparse.ArgumentParser(
        description="Find the next satellite overpass date for a given point",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=EXAMPLE,
    )

    parser.add_argument("--latitude", "-lat", required=True, type=float, help="Latitude of the point")
    parser.add_argument("--longitude", "-lon", required=True, type=float, help="Longitude of the point")
    parser.add_argument(
        "--satellite", "-sat", required=True, type=str,
        choices=["sentinel-1", "sentinel-2", "landsat"],
        help="Satellite mission: sentinel-1, sentinel-2, or landsat"
    )
    parser.add_argument("--granule", "-g", type=str, help="Granule name for Sentinel-1 processing")
    parser.add_argument("--log_level", "-l", default="info", type=str, help="Log level")
    return parser

def get_granule_info(granule: str) -> Tuple[Polygon, str, int]:
    """Retrieve granule information from ASF API."""
    try:
        result = asf.granule_search(granule)[0]

        footprint = shape(result.geometry)
        mode = result.properties["beamModeType"]
        orbit_relative = result.properties["pathNumber"]
        return footprint, mode, orbit_relative
    except Exception as e:
        LOGGER.error(f"Error retrieving granule info: {e}")
        raise

def find_valid_insar_collects(
    collections: gpd.GeoDataFrame, mode: str, orbit_relative: int
) -> gpd.GeoDataFrame:
    """Filter collections by mode and orbit relative number."""
    return collections.loc[
        (collections["orbit_relative"] == orbit_relative) & (collections["mode"] == mode)
    ]

def find_valid_collect(
    gdf: gpd.GeoDataFrame, footprint: Union[Polygon, Point], mode=None
) -> Tuple[bool, Optional[datetime]]:
    """Find valid collects intersecting a footprint."""
    gdf = gdf.loc[gdf["geometry"].intersects(footprint)].copy()

    if not gdf.empty:
        gdf = gdf.sort_values("begin_date", ascending=True).reset_index(drop=True)
        #return True, gdf["begin_date"].iloc[0]
        return True, gdf["begin_date"].tolist(), gdf["orbit_relative"].tolist(), gdf["geometry"].tolist()
    return False, None

def get_next_collect(
    point: Point, collection_dataset: gpd.GeoDataFrame, mode: Optional[str] = None
) -> str:
    """Get the next collect for a given point and optional mode."""
    if mode:
        collection_dataset = collection_dataset.loc[collection_dataset["mode"] == mode].copy()
        mode_msg = f" {mode} "
    else:
        mode_msg = " "

    collect_scheduled, next_collect, next_collect_orbit, next_collect_geometry  = find_valid_collect(collection_dataset, point)
    if collect_scheduled:
        #return f"Next{mode_msg}collect is {next_collect[0].strftime('%Y-%m-%d %H:%M:%S')}"
        #return f"Next{mode_msg} collect is: " + ", ".join(dt.strftime('%Y-%m-%d %H:%M:%S') for dt in next_collect)
        return(format_collection_outputs(next_collect,next_collect_orbit))
    max_date = collection_dataset["end_date"].max().date()
    return f"No{mode_msg}collect is scheduled on or before {max_date}"

def find_next_sentinel1_overpass(
    granule: str, collection_dataset: gpd.GeoDataFrame
) -> str:
    """Find the next interferometric collect for Sentinel-1."""
    footprint, mode, orbit_relative = get_granule_info(granule)
    valid_insar_collects = find_valid_insar_collects(collection_dataset, mode, orbit_relative)
    return get_next_collect(footprint, valid_insar_collects)

def find_next_sentinel2_overpass(
    latitude: float, longitude: float, collection_dataset: gpd.GeoDataFrame
) -> str:
    """Find the next overpass for Sentinel-2 using its acquisition plans."""
    point = Point(longitude, latitude)
    collect_scheduled, next_collect, next_collect_orbit, next_collect_geometry  = find_valid_collect(collection_dataset, point)
    if collect_scheduled:
        #return f"Next Sentinel-2 collect is {next_collect.strftime('%Y-%m-%d %H:%M:%S')}"
        #return f"Next collect is: " + ", ".join(dt.strftime('%Y-%m-%d %H:%M:%S') for dt in next_collect)
        return(format_collection_outputs(next_collect,next_collect_orbit))
    max_date = collection_dataset["end_date"].max().date()
    return f"No Sentinel-2 collect is scheduled on or before {max_date}"

def find_next_overpass(
    latitude: float, longitude: float, satellite: str, granule: Optional[str] = None
) -> str:
    """Find the next overpass for the given satellite and location."""
    if satellite == "sentinel-1":
        LOGGER.info("Processing Sentinel-1 data...")
        collection_path = create_s1_collection_plan()
        gdf = gpd.read_file(collection_path)
        if granule:
            return find_next_sentinel1_overpass(granule, gdf)
        point = Point(longitude, latitude)
        return get_next_collect(point, gdf)

    if satellite == "sentinel-2":
        LOGGER.info("Processing Sentinel-2 data...")
        collection_path = create_s2_collection_plan()
        gdf = gpd.read_file(collection_path)
        return find_next_sentinel2_overpass(latitude, longitude, gdf)

    if satellite == "landsat":
        LOGGER.info("Fetching Landsat overpass information...")
        return next_landsat_pass(latitude, longitude)

    LOGGER.error("Unsupported satellite: %s", satellite)
    return "Unsupported satellite."

if __name__ == "__main__":
    parser = create_parser()
    args = parser.parse_args()

    log_level = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
    }.get(args.log_level.lower(), logging.INFO)

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    result = find_next_overpass(args.latitude, args.longitude, args.satellite, args.granule)
    print(result)
