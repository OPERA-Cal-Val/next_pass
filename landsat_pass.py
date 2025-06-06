import logging
from datetime import date, datetime

import argparse
import requests
from tabulate import tabulate
from utils import arcgis_to_polygon

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Base URLs
MAP_SERVICE_URL = (
    "https://nimbus.cr.usgs.gov/arcgis/rest/services/LLook_Outlines/MapServer/1/"
)
JSON_URL = "https://landsat.usgs.gov/sites/default/files/landsat_acq/assets/json/cycles_full.json"


def ll2pr(lat: float, lon: float, session: requests.Session) -> dict:
    """
    Convert latitude and longitude to Path/Row and their geometries for ascending and descending directions.

    Args:
        lat (float): Latitude.
        lon (float): Longitude.
        session (requests.Session): HTTP session object.

    Returns:
        dict: Dictionary with 'ascending' and 'descending' data, including path/row and geometry.
    """
    results = {"ascending": None, "descending": None}
    directions = {"ascending": "A", "descending": "D"}

    for direction, mode in directions.items():
        query_url = (
            f"{MAP_SERVICE_URL}query?where=MODE='{mode}'"
            f"&geometry={lon},{lat}"
            "&geometryType=esriGeometryPoint&spatialRel=esriSpatialRelIntersects"
            "&outFields=PATH,ROW"
            "&returnGeometry=true&f=json"
        )

        try:
            response = session.get(query_url, timeout=10)
            response.raise_for_status()
            data = response.json()

            if not data.get("features"):
                results[direction] = None
                continue

            features = []
            for feature in data["features"]:
                attributes = feature["attributes"]
                geometry = feature.get("geometry")
                features.append({
                    "path": attributes["PATH"],
                    "row": attributes["ROW"],
                    "geometry": geometry
                })

            results[direction] = features

        except requests.RequestException as error:
            logging.error(f"Error fetching data for "
                          f"{direction.capitalize()} direction: {error}")
            results[direction] = None

    return results



def find_next_landsat_pass(path: int, session: requests.Session, num_passes: int = 5) -> dict:
    """
    Find the next Landsat-8 and Landsat-9 passes for a given path.

    Args:
        path (int): WRS-2 path number.
        session (requests.Session): HTTP session object.
        num_passes (int): Number of future passes to find (default is 5).

    Returns:
        dict: Dictionary with next pass dates for each mission.
    """
    try:
        response = session.get(JSON_URL, timeout=10)
        response.raise_for_status()
        cycles_data = response.json()
    except requests.RequestException as error:
        logging.error(f"Error fetching cycles data: {error}")
        return {"landsat_8": [], "landsat_9": []}

    next_passes = {"landsat_8": [], "landsat_9": []}
    today = date.today()

    for mission in next_passes:
        if mission not in cycles_data:
            logging.warning(f"Mission {mission} not found in JSON data.")
            continue

        sorted_dates = sorted(
            cycles_data[mission].items(),
            key=lambda x: datetime.strptime(x[0], "%m/%d/%Y")
        )

        for date_str, details in sorted_dates:
            pass_date = datetime.strptime(date_str, "%m/%d/%Y").date()

            if pass_date >= today and str(path) in details["path"].split(","):
                next_passes[mission].append(date_str)
                if len(next_passes[mission]) >= num_passes:
                    break

    return next_passes


def next_landsat_pass(lat: float, lon: float) -> None:
    """
    Main function to retrieve and display the next Landsat passes for a given location.

    Args:
        lat (float): Latitude.
        lon (float): Longitude.
    """
    session = requests.Session()

    try:
        results = ll2pr(lat, lon, session=session)
        table_data = []
        geometry_data = []

        for direction, features in results.items():
            if features:
                for feature in features:
                    path = feature["path"]
                    row = feature["row"]
                    geometry = feature.get("geometry")
                    polygon = arcgis_to_polygon(geometry)
                    next_pass_dates = find_next_landsat_pass(path, session=session, num_passes=5)
                    for mission, dates in next_pass_dates.items():
                        row_data = [
                            direction.capitalize(),
                            path,
                            row,
                            mission.capitalize(),
                            ", ".join(dates) if dates else "No future passes found."
                        ]
                        table_data.append(row_data)
                        if polygon:
                            geometry_data.append(polygon)
            else:
                table_data.append(
                    [direction.capitalize(), "N/A", "N/A", "N/A", "No data found."]
                )

        return {"next_collect_info": tabulate(
            table_data,
            headers=["Direction", "Path", "Row", "Mission", "Next Passes"],
            tablefmt="grid"
            ),
            "next_collect_geometry": geometry_data
            }

    except Exception as error:
        logging.exception(f"An unexpected error occurred: {error}")
    finally:
        session.close()


def parse_args() -> argparse.Namespace:
    """
    Parse command-line arguments.

    Returns:
        argparse.Namespace: Parsed arguments.
    """
    parser = argparse.ArgumentParser(
        description="Find next Landsat satellite overpasses for a given latitude and longitude."
    )
    parser.add_argument(
        "--lat", type=float, required=True, help="Latitude of the location."
    )
    parser.add_argument(
        "--lon", type=float, required=True, help="Longitude of the location."
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    result = next_landsat_pass(args.lat, args.lon)
    if result:
        print(result.get("next_collect_info", "No collection info available."))
