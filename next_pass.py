#!/usr/bin/env python3
import argparse
import logging
import geopandas as gpd
from shapely.geometry import Point, box
from tabulate import tabulate
from landsat_pass import next_landsat_pass
from s1_collection import create_s1_collection_plan
from s2_collection import create_s2_collection_plan
from utils import (create_polygon_from_kml,
                   find_intersecting_collects, bbox_type)

LOGGER = logging.getLogger("next_pass")


def create_parser():
    parser = argparse.ArgumentParser(
        description="Find next satellite overpass date.")
    parser.add_argument("-b", "--bbox", required=True, type=float, nargs=4,
                        metavar=("lat_min", "lat_max", "lon_min", "lon_max"),
                        help=("Bounding box coordinates (SNWE order)."
                              "A single point can be given as "
                              "equal values for SN and EW"))
    parser.add_argument("-s", "--satellite", required=True,
                        choices=["sentinel-1", "sentinel-2", "landsat"],
                        help="Satellite mission")
    parser.add_argument("-f", "--aoi-file", type=str,
                        help="Path to KML file for AOI polygon")
    parser.add_argument("-l", "--log_level", default="info",
                        choices=["debug", "info", "warning", "error"],
                        help="Set logging level")
    return parser


def format_collects(gdf):
    table = [(idx + 1, row.begin_date.strftime("%Y-%m-%d %H:%M:%S"),
              row.orbit_relative)
             for idx, row in gdf.iterrows()]
    headers = ["#", "Collection Date & Time", "Relative Orbit"]
    return tabulate(table, headers, tablefmt="grid")


def find_next_overpass(args):
    lat_min, lat_max, lon_min, lon_max = bbox_type(args.bbox)
    if args.aoi_file:
        geometry = create_polygon_from_kml(args.aoi_file)
    elif lat_min == lat_max and lon_min == lon_max:
        geometry = Point(lon_min, lat_min)
    else:
        geometry = box(lon_min, lat_min, lon_max, lat_max)

    # Initialize the return dictionary with default values
    result = {
        "next_collect_info": None,
        "next_collect_geometry": None
    }
    if args.satellite == "sentinel-1":
        LOGGER.info("Processing Sentinel-1 data...")
        gdf = gpd.read_file(create_s1_collection_plan())
        collects = find_intersecting_collects(gdf, geometry)

        if not collects.empty:
            result["next_collect_info"] = format_collects(collects)
            result["next_collect_geometry"] = collects.geometry.tolist()  # Convert geometries to list
        else:
            result["next_collect_info"] = f"No scheduled collect before {gdf['end_date'].max().date()}."
            result["next_collect_geometry"] = None  # Set to None if no collections

    elif args.satellite == "sentinel-2":
        LOGGER.info("Processing Sentinel-2 data...")
        gdf = gpd.read_file(create_s2_collection_plan())
        collects = find_intersecting_collects(gdf, geometry)

        if not collects.empty:
            result["next_collect_info"] = format_collects(collects)
            result["next_collect_geometry"] = collects.geometry.tolist()  # Convert geometries to list
        else:
            result["next_collect_info"] = f"No scheduled collect before {gdf['end_date'].max().date()}."
            result["next_collect_geometry"] = None  # Set to None if no collections

    elif args.satellite == "landsat":
        LOGGER.info("Fetching Landsat data...")
        return next_landsat_pass(lat_min, lon_min)
        if next_pass_geometry:
            result["next_collect_info"] = "Landsat next pass data available"
            result["next_collect_geometry"] = [next_pass_geometry]  # Wrap in list for consistency
        else:
            result["next_collect_info"] = "No scheduled Landsat collect before the given date."
            result["next_collect_geometry"] = None  # No geometry available

    return result

if __name__ == "__main__":
    args = create_parser().parse_args()
    logging.basicConfig(level=args.log_level.upper(),
                        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    if args.satellite.lower() == 'landsat':
        find_next_overpass(args)
    else:
        print(find_next_overpass(args).get("next_collect_info", "No collection info available"))
