#!/usr/bin/env python3

import argparse
import logging

from shapely.geometry import Point, box

from landsat_pass import next_landsat_pass
from sentinel_pass import (
    next_sentinel_pass,
    create_s1_collection_plan,
    create_s2_collection_plan,
)
from utils import bbox_type, create_polygon_from_kml
from opera_products import (
    find_print_available_opera_products,
    export_opera_products,
    make_opera_granule_map,
)

LOGGER = logging.getLogger("next_pass")

EXAMPLE = """
EXAMPLE USAGE:
Point (lat/lon pair):
  python next_pass.py -b 34.20 -118.17

Bounding Box (SNWE):
  python next_pass.py -b 34.15 34.25 -118.20 -118.15

KML File:
  python next_pass.py -b /path/to/file.kml
"""


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for CLI inputs."""
    desc = "Find next satellite overpass date."
    parser = argparse.ArgumentParser(
        description=desc,
        epilog=EXAMPLE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-b",
        "--bbox",
        required=True,
        nargs="+",
        type=str,
        help="Bounding box: Either 2 or 4 floats (point or bbox) "
        "or a path to a .kml location file",
    )
    parser.add_argument(
        "-s",
        "--sat",
        default="all",
        choices=["sentinel-1", "sentinel-2", "landsat", "all"],
        help="Satellite mission. Default is all.",
    )
    parser.add_argument(
        "-n",
        "--ngr",
        default=5,
        type=int,
        help="Number of mots recent granules to consider for OPERA products",
    )
    parser.add_argument(
        "-l",
        "--log_level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Set logging level (default: info).",
    )
    return parser


def find_next_overpass(args) -> dict:
    """Main logic for finding the next satellite overpasses."""
    bbox = bbox_type(args.bbox)

    if isinstance(bbox, str):
        # create geometry for Sentinel-1 and 2 and point (centroid) for Landsat
        geometry = create_polygon_from_kml(bbox)
        centroid = geometry.centroid
        lat_min = centroid.y
        lon_min = centroid.x
    else:
        lat_min, lat_max, lon_min, lon_max = bbox
        if lat_min == lat_max and lon_min == lon_max:
            geometry = Point(lon_min, lat_min)
        else:
            geometry = box(lon_min, lat_min, lon_max, lat_max)

    if args.sat == "all":
        LOGGER.info("Fetching Sentinel-1 data...")
        sentinel1 = next_sentinel_pass(create_s1_collection_plan, geometry)

        LOGGER.info("Fetching Sentinel-2 data...")
        sentinel2 = next_sentinel_pass(create_s2_collection_plan, geometry)

        LOGGER.info("Fetching Landsat data...")
        landsat = next_landsat_pass(lat_min, lon_min)

        return {
            "sentinel-1": sentinel1,
            "sentinel-2": sentinel2,
            "landsat": landsat,
        }

    if args.sat == "sentinel-1":
        LOGGER.info("Fetching Sentinel-1 data...")
        return next_sentinel_pass(create_s1_collection_plan, geometry)

    if args.sat == "sentinel-2":
        LOGGER.info("Fetching Sentinel-2 data...")
        return next_sentinel_pass(create_s2_collection_plan, geometry)

    if args.sat == "landsat":
        LOGGER.info("Fetching Landsat data...")
        return next_landsat_pass(lat_min, lon_min)

    raise ValueError(
        "Satellite not recognized. "
        "Supported values: sentinel-1, sentinel-2, landsat, all."
    )


def main():
    """Main entry point."""
    args = create_parser().parse_args()

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    result = find_next_overpass(args)

    if isinstance(result, dict) and "sentinel-1" in result:
        # Case: satellite == all
        for mission, mission_result in result.items():
            print(f"\n=== {mission.upper()} ===")
            print(
                mission_result.get(
                    "next_collect_info",
                    "No collection info available."
                )
            )
    else:
        # Case: only one satellite selected
        print(result.get("next_collect_info", "No collection info available."))

    # search for & print OPERA results
    results_opera = find_print_available_opera_products(args)
    export_opera_products(results_opera)
    make_opera_granule_map(results_opera, args)


if __name__ == "__main__":
    main()
