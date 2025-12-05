import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

import requests
from shapely.geometry import Point, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union
from tabulate import tabulate

from utils.utils import arcgis_to_polygon

logger = logging.getLogger(__name__)

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

    if isinstance(geometry, Polygon):
        coords = list(geometry.exterior.coords)
        rings = [[[x, y] for x, y in coords]]  # [ [lon, lat], ... ]
        esri_geom = {"rings": rings, "spatialReference": {"wkid": 4326}}
        return json.dumps(esri_geom), "esriGeometryPolygon"

    msg = "Unsupported geometry type. Only Point and Polygon are supported."
    raise ValueError(msg)


def ll2pr(geometry: BaseGeometry, session: requests.Session) -> dict:
    """
    Convert a Shapely geometry (Point or Polygon) to Path/Row and their geometries.

    Args:
        geometry (BaseGeometry): Shapely Point or Polygon.
        session (requests.Session): HTTP session object.

    Returns:
        dict: Dictionary with 'ascending' and 'descending' data.
    """
    results: dict[str, list | None] = {"ascending": None, "descending": None}
    directions = {"ascending": "A", "descending": "D"}

    geometry_json, geometry_type = shapely_to_esri_json(geometry)

    for direction, mode in directions.items():
        query_url = f"{MAP_SERVICE_URL}query"
        params = {
            "where": f"MODE='{mode}'",
            "geometryType": geometry_type,
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "PATH,ROW",
            "returnGeometry": "true",
            "f": "json",
        }

        try:
            response = session.post(
                query_url,
                params=params,
                data={"geometry": geometry_json},
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("features"):
                results[direction] = None
                continue

            features = []
            for feature in data["features"]:
                attributes = feature["attributes"]
                geom = feature.get("geometry")
                features.append(
                    {
                        "path": attributes["PATH"],
                        "row": attributes["ROW"],
                        "geometry": geom,
                    }
                )

            results[direction] = features

        except requests.RequestException as error:
            logger.error(
                "Error fetching data for %s direction: %s",
                direction.capitalize(),
                error,
            )
            results[direction] = None

    return results


def find_next_landsat_pass(
    path: int,
    n_day_past: float,
    session: requests.Session,
    num_passes: int = 5,
) -> dict:
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
        logger.error("Error fetching cycles data: %s", error)
        return {"landsat_8": [], "landsat_9": []}

    next_passes = {"landsat_8": [], "landsat_9": []}
    today = date.today()
    n_days_earlier = today - timedelta(days=n_day_past)

    for mission in next_passes:
        if mission not in cycles_data:
            logger.warning("Mission %s not found in JSON data.", mission)
            continue

        sorted_dates = sorted(
            cycles_data[mission].items(),
            key=lambda x: datetime.strptime(x[0], "%m/%d/%Y"),
        )

        for date_str, details in sorted_dates:
            pass_date = datetime.strptime(date_str, "%m/%d/%Y").date()

            if pass_date >= n_days_earlier and str(path) in details["path"].split(","):
                next_passes[mission].append(date_str)
                if len(next_passes[mission]) >= num_passes:
                    break

    return next_passes


def next_landsat_pass(
    lat: float,
    lon: float,
    geometryAOI,
    n_day_past: float,
) -> dict | None:
    """
    Retrieve and format the next Landsat passes for a given location.

    Args:
        lat (float): Latitude.
        lon (float): Longitude.
        geometryAOI: Geometry of the area of interest used for computing
            intersection percentage.
        n_day_past (float): Number of days in the past to search cycles JSON.

    Returns:
        dict or None: Dictionary containing next Landsat passes information
        and geometries, or None on failure.
    """
    session = requests.Session()

    try:
        results = ll2pr(geometryAOI, session=session)
        aggregated_data = defaultdict(
            lambda: {"rows": set(), "overlap_pct": 0.0, "dates": None}
        )
        geometry_groups = defaultdict(list)

        for direction, features in results.items():
            if features:
                for feature in features:
                    path = feature["path"]
                    row = feature["row"]
                    geom = feature.get("geometry")
                    polygon = arcgis_to_polygon(geom)

                    if geometryAOI.type == "Point":
                        intersection_pct = 100
                    elif polygon and polygon.is_valid and geometryAOI.is_valid:
                        intersection = polygon.intersection(geometryAOI)
                        intersection_pct = 100 * (intersection.area / geometryAOI.area)
                    else:
                        intersection_pct = 0.0

                    next_pass_dates = find_next_landsat_pass(
                        path,
                        n_day_past,
                        session=session,
                        num_passes=5,
                    )
                    for mission, dates in next_pass_dates.items():
                        key = (direction.capitalize(), path, mission.capitalize())
                        aggregated_data[key]["rows"].add(row)
                        aggregated_data[key]["overlap_pct"] += intersection_pct
                        if aggregated_data[key]["dates"] is None:
                            aggregated_data[key]["dates"] = dates

                        if polygon:
                            geometry_groups[key].append(polygon)
            else:
                key = (direction.capitalize(), "N/A", "N/A")
                aggregated_data[key]["rows"].add("N/A")
                aggregated_data[key]["dates"] = []
                aggregated_data[key]["overlap_pct"] = 0.0

        row_data_with_keys = []
        date_format = "%m/%d/%Y"

        for key, data in aggregated_data.items():
            direction, path, mission = key
            row_list = sorted(data["rows"])
            rows_str = ", ".join(str(r) for r in row_list)
            overlap = data["overlap_pct"]
            overlap_str = f"{overlap:.2f}%" if overlap > 0 else "N/A"

            if data["dates"]:
                dates_str = ", ".join(
                    date_str
                    + (
                        " (P)"
                        if datetime.strptime(date_str, date_format) < datetime.now()
                        else ""
                    )
                    for date_str in data["dates"]
                )
            else:
                dates_str = "No future passes found."

            row_data = [direction, path, rows_str, mission, dates_str, overlap_str]

            # Include key for geometry ordering
            row_data_with_keys.append((overlap, row_data, key))

        sorted_row_data = sorted(row_data_with_keys, key=lambda x: x[0], reverse=True)
        table_data = [row for _, row, _ in sorted_row_data]
        geometry_keys = [key for _, _, key in sorted_row_data]

        geometry_data = []
        for key in geometry_keys:
            polygons = geometry_groups.get(key, [])
            if polygons:
                merged = unary_union(polygons)
                geometry_data.append(merged)

        return {
            "next_collect_info": tabulate(
                table_data,
                headers=[
                    "Direction",
                    "Path",
                    "Row",
                    "Mission",
                    "Passes UTC dates (P for past)",
                    "AOI % Overlap",
                ],
                tablefmt="grid",
            ),
            "next_collect_geometry": geometry_data,
        }

    except Exception as error:  # noqa: BLE001
        logger.exception("An unexpected error occurred: %s", error)
        return None
    finally:
        session.close()
