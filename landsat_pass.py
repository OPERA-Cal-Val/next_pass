import logging
from datetime import date, datetime
import argparse
import requests
import json
import urllib.parse
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
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

def shapely_to_esri_json(geometry: BaseGeometry) -> tuple[str, str]:
    """
    Convert a Shapely geometry to Esri JSON format and return geometryType.
    Args:
        geometry (BaseGeometry): A Shapely Point or Polygon.
    Returns:
        tuple: (Esri JSON geometry string, geometry type)
    """
    if isinstance(geometry, Point):
        coords = f"{geometry.x},{geometry.y}"
        return coords, "esriGeometryPoint"

    elif isinstance(geometry, Polygon):
        coords = list(geometry.exterior.coords)
        # Ensure list of [ [lon, lat], ... ]
        rings = [[[x, y] for x, y in coords]]
        esri_geom = {
            "rings": rings,
            "spatialReference": {"wkid": 4326}
        }
        return urllib.parse.quote(json.dumps(esri_geom)), "esriGeometryPolygon"

    else:
        raise ValueError("Unsupported geometry type. Only Point and Polygon are supported.")


def ll2pr(geometry: BaseGeometry, session: requests.Session) -> dict:
    """
    Convert a Shapely geometry (Point or Polygon) to Path/Row
    and their geometries.

    Args:
        geometry (BaseGeometry): Shapely Point or Polygon.
        session (requests.Session): HTTP session object.

    Returns:
        dict: Dictionary with 'ascending' and 'descending' data.
    """
    results = {"ascending": None, "descending": None}
    directions = {"ascending": "A", "descending": "D"}

    geometry_str, geometry_type = shapely_to_esri_json(geometry)

    for direction, mode in directions.items():
        query_url = (
            f"{MAP_SERVICE_URL}query?where=MODE='{mode}'"
            f"&geometry={geometry_str}"
            f"&geometryType={geometry_type}&spatialRel=esriSpatialRelIntersects"
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
            logging.error(f"Error fetching data for {direction.capitalize()} direction: {error}")
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


def next_landsat_pass(lat: float, lon: float, geometryAOI) -> None:
    """
    Main function to retrieve and display the next Landsat passes for a given location.

    Args:
        lat (float): Latitude.
        lon (float): Longitude.
        geometryAOI: Geometry of the area of interest used for computing intersection percentage.
    Returns:
        dict: Dictionary containing next Landsat passes information and geometries.
    """
    session = requests.Session()

    try:
        results = ll2pr(geometryAOI, session=session)
        table_data = []
        geometry_data = []

        for direction, features in results.items():
            if features:
                for feature in features:
                    path = feature["path"]
                    row = feature["row"]
                    geometry = feature.get("geometry")
                    polygon = arcgis_to_polygon(geometry)

                    if polygon and polygon.is_valid and geometryAOI.is_valid:
                        intersection = polygon.intersection(geometryAOI)
                        intersection_pct = 100 * (intersection.area / geometryAOI.area)
                        intersection_str = f"{intersection_pct:.2f}%"
                    else:
                        intersection_str = "N/A"

                    next_pass_dates = find_next_landsat_pass(path, session=session, num_passes=5)
                    for mission, dates in next_pass_dates.items():
                        row_data = [
                            direction.capitalize(),
                            path,
                            row,
                            mission.capitalize(),
                            ", ".join(dates) if dates else "No future passes found.",
                            intersection_str
                        ]
                        table_data.append(row_data)
                        if polygon:
                            geometry_data.append(polygon)
            else:
                table_data.append(
                    [direction.capitalize(), "N/A", "N/A", "N/A", "No data found.", "N/A"]
                )

        return {"next_collect_info": tabulate(
            table_data,
            headers=["Direction", "Path", "Row", "Mission", "Next Passes", "AOI % Overlap"],
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
