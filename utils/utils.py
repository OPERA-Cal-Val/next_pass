import argparse
import logging
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple, Union
from urllib.parse import urljoin
from shapely.geometry import shape, Polygon
from shapely import LinearRing, Point
from lxml import etree
from bs4 import BeautifulSoup
import geopandas as gpd
import requests
from bs4 import BeautifulSoup
from lxml import etree
from shapely import LinearRing, Point, Polygon
from shapely.geometry import shape
from timezonefinder import TimezoneFinder
from zoneinfo import ZoneInfo

LOGGER = logging.getLogger("acquisition_utils")


class Tee:
    """Write to multiple streams (e.g., terminal and log file)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, message):
        for stream in self.streams:
            stream.write(message)
            stream.flush()

    def flush(self):
        for stream in self.streams:
            stream.flush()


def scrape_esa_download_urls(url: str, class_: str) -> List[str]:
    """Scrape ESA website for KML download URLs."""
    response = requests.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    div = soup.find("div", class_=class_)
    hrefs = [a["href"] for a in div.find_all("a", href=True)]
    clean_hrefs = []
    for href in hrefs:
        if href.startswith("https://sentinel/"):
            # Fix malformed domain
            href = href.replace("https://sentinel", "")
        clean_hrefs.append(urljoin("https://sentinels.copernicus.eu", href))

    return clean_hrefs


def download_kml(url: str, out_path: str = "collection.kml") -> Path:
    """Download a KML file from a URL."""
    response = requests.get(url)
    response.raise_for_status()
    path = Path(out_path)
    path.write_bytes(response.content)
    LOGGER.info(f"File downloaded successfully: {path}")
    return path


def parse_placemark(placemark: etree.Element) -> Optional[Tuple]:
    """Parse a single placemark from KML."""
    ns = ".//{http://www.opengis.net/kml/2.2}"
    begin_date = datetime.fromisoformat(placemark.find(f"{ns}begin").text)
    end_date = datetime.fromisoformat(placemark.find(f"{ns}end").text)

    data = placemark.find(f"{ns}ExtendedData")
    mode = data.find(f"{ns}Data[@name='Mode']/{ns}value").text
    orbit_absolute = int(data.find(f"{ns}Data[@name='OrbitAbsolute']/{ns}value").text)
    orbit_relative = int(data.find(f"{ns}Data[@name='OrbitRelative']/{ns}value").text)

    coords_text = placemark.find(f"{ns}LinearRing/{ns}coordinates").text.strip()
    coords = [tuple(map(float, coord.split(",")[:2])) for coord in coords_text.split()]
    footprint = Polygon(LinearRing(coords))

    return (begin_date, end_date, mode, orbit_absolute, orbit_relative, footprint)


def parse_kml(kml_path: Path) -> gpd.GeoDataFrame:
    """Parse a KML file into a GeoDataFrame."""
    tree = etree.parse(kml_path)
    placemarks = [
        parse_placemark(elem)
        for elem in tree.findall(".//{http://www.opengis.net/kml/2.2}Placemark")
    ]
    placemarks = [p for p in placemarks if p]

    columns = [
        "begin_date",
        "end_date",
        "mode",
        "orbit_absolute",
        "orbit_relative",
        "geometry",
    ]
    return gpd.GeoDataFrame(
        placemarks, columns=columns, geometry="geometry", crs="EPSG:4326"
    )


def parse_kml_polygon_coords(kml_file: Path) -> List[Tuple[float, float]]:
    """Extract coordinates from a KML polygon."""
    tree = ET.parse(kml_file)
    ns = {"kml": "http://www.opengis.net/kml/2.2"}
    coords_text = tree.find(".//kml:Placemark//kml:coordinates", ns).text.strip()
    return [tuple(map(float, coord.split(",")[:2])) for coord in coords_text.split()]


def create_polygon_from_kml(kml_file: Path) -> Optional[Polygon]:
    """Create a Shapely polygon from a KML file."""
    coordinates = parse_kml_polygon_coords(kml_file)
    return Polygon(coordinates) if coordinates else None


def find_intersecting_collects(
    gdf: gpd.GeoDataFrame,
    geometryAOI: Union[Polygon, Point],
    mode: Optional[str] = None,
    orbit_relative: Optional[int] = None,
) -> gpd.GeoDataFrame:
    intersects = gdf[gdf.intersects(geometryAOI)].copy()

    if mode:
        intersects = intersects[intersects["mode"] == mode]
    if orbit_relative is not None:
        intersects = intersects[intersects["orbit_relative"] == orbit_relative]

    if geometryAOI.type == "Point":
        overlap = 100
    else:
        overlap = 100 * (
            intersects.geometry.intersection(geometryAOI).area / geometryAOI.area
        )
    intersects["intersection_pct"] = overlap

    return intersects.sort_values(
        ["intersection_pct", "begin_date"], ascending=[False, True]
    ).reset_index(drop=True)


def bbox_type(arg_coords):
    """Parses and validates bounding box input from command line.

    Supports:
    - Path to a .kml file
    - Two floats (point: lat lon)
    - Four floats (bounding box: lat_min lat_max lon_min lon_max)

    Returns:
        str or tuple: KML file path, or a bounding box tuple in the format:
        (lat_min, lat_max, lon_min, lon_max)
    """
    if isinstance(arg_coords, str):
        arg_coords = [arg_coords]

    if (
        len(arg_coords) == 1
        and arg_coords[0].lower().endswith(".kml")
        and os.path.isfile(arg_coords[0])
    ):
        return arg_coords[0]

    try:
        coords = [float(x) for x in arg_coords]
        if len(coords) not in (2, 4):
            raise argparse.ArgumentTypeError(
                "Must provide either a lat/lon pair for a point or SNWE "
                "(lat_min lat_max lon_min lon_max) for a bbox."
            )

        if len(coords) == 2:
            lat_min, lon_min = coords
            lat_max, lon_max = lat_min, lon_min
        else:
            lat_min, lat_max, lon_min, lon_max = coords

        if not (-90 <= lat_min <= 90 and -90 <= lat_max <= 90):
            raise argparse.ArgumentTypeError(
                f"Latitudes must be between -90 and 90 degrees. "
                f"Got: {lat_min}, {lat_max}"
            )
        if not (-180 <= lon_min <= 180 and -180 <= lon_max <= 180):
            raise argparse.ArgumentTypeError(
                f"Longitudes must be between -180 and 180 degrees. "
                f"Got: {lon_min}, {lon_max}"
            )

        if lat_min > lat_max:
            LOGGER.warning(
                "Minimum latitude %.6f is greater than "
                "maximum %.6f; swapping values.",
                lat_min,
                lat_max,
            )
            lat_min, lat_max = lat_max, lat_min

        if lon_min > lon_max:
            LOGGER.warning(
                "Minimum longitude %.6f is greater "
                "than maximum %.6f; swapping values.",
                lon_min,
                lon_max,
            )
            lon_min, lon_max = lon_max, lon_min

        return lat_min, lat_max, lon_min, lon_max

    except ValueError:
        raise argparse.ArgumentTypeError(
            "Provide either 2 or 4 float values " "or a path to a valid .kml file."
        )


def arcgis_to_polygon(geometry):
    rings = geometry.get("rings")
    if not rings:
        return None
    # Use the first ring as the exterior boundary
    return Polygon(rings[0])


def get_spatial_extent_km(polygon_geojson):
    # create a geodataframe from our polygon
    geom = shape(polygon_geojson)
    gdf = gpd.GeoDataFrame(geometry=[geom], crs="EPSG:4326")
    # Project to metric CRS (Web Mercator: EPSG 3857) to calculate in meters
    gdf_proj = gdf.to_crs(epsg=3857)
    # Get total bounds: [minx, miny, maxx, maxy]
    minx, miny, maxx, maxy = gdf_proj.total_bounds

    # Width and height in meters
    width_m = maxx - minx
    height_m = maxy - miny

    # Convert to kilometers
    width_km = width_m / 1000
    height_km = height_m / 1000

    return {
        "width_km": width_km,
        "height_km": height_km,
        "area_km2": gdf_proj.geometry.area.sum() / 1e6,  
    }


def is_date_in_text(iso_date_str: str, text: str) -> bool:
    """
    Check if the date (YYYY-MM-DD) from an ISO timestamp
    is present anywhere in the given text.

    Handles ISO timestamps with or without milliseconds, e.g.:
    - '2025-10-21T22:39:01.066Z'
    - '2025-10-10T04:41:14Z'

    Args:
        iso_date_str (str): ISO timestamp string.
        text (str): Text to search for the date.

    Returns:
        bool: True if the date exists in the text, False otherwise.
    """
    # Remove trailing 'Z' if present
    iso_date_str = iso_date_str.rstrip("Z")

    # Parse ISO timestamp, handle milliseconds or not
    try:
        parsed_date = datetime.strptime(iso_date_str, "%Y-%m-%dT%H:%M:%S.%f")
    except ValueError:
        parsed_date = datetime.strptime(iso_date_str, "%Y-%m-%dT%H:%M:%S")

    # Extract YYYY-MM-DD
    date_only_str = parsed_date.strftime("%Y-%m-%d")

    # Find all date-like patterns in the text
    dates_in_text = re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text)

    return date_only_str in dates_in_text


def style_function_factory(dataset_color: str,
                           inactive_color: str = "lightgray"):
    def style_function(feature):
        ok = feature["properties"].get("condition_ok")
        if ok is True:
            return {
                "color": dataset_color,
                "fillColor": dataset_color,
                "weight": 2,
                "fillOpacity": 0.5,
            }
        else:
            return {
                "color": inactive_color,
                "fillColor": inactive_color,
                "weight": 1,
                "fillOpacity": 0.3,
            }
    return style_function


def valid_drcs_datetime(s):
    try:
        dt = datetime.strptime(s, "%Y-%m-%dT%H:%M")
        # Add system local timezone (makes it offset-aware)
        local_tz = datetime.now().astimezone().tzinfo
        return dt.replace(tzinfo=local_tz)

    except ValueError:
        raise argparse.ArgumentTypeError(
            f"Invalid DRCS date-time format: '{s}'. "
            "Expected format: YYYY-MM-DDTHH:MM"
        )


def check_opera_overpass_intersection(product_label, product_geom,
                                      result_s1, result_s2,
                                      result_l, event_date):
    """
    Check if a given product overlaps with any satellite overpass
    after the event date, and produce a formatted text report.

    Args:
        product_label (str): e.g. 'OPERA_product_20251116_S1_...'
        product_geom (shapely Polygon): product intersection with the AOI
        result_s1, result_s2, result_l (dict): overpass info dicts
        event_date (datetime): the event datetime

    Returns:
        str: formatted report of past recent and future overlapping overpasses
    """

    # first determine satellite
    parts = product_label.split("_")
    input_sat = parts[6]

    if "S1" in input_sat:
        result = result_s1
        sat_name = "Sentinel-1"
    elif "S2" in input_sat:
        result = result_s2
        sat_name = "Sentinel-2"
    elif "L8" in input_sat or "L9" in input_sat:
        result = result_l
        sat_name = "Landsat"
    else:
        return f"Unknown satellite for product {product_label}"

    if not result:
        return f"No overpass results available for {sat_name}"

    info_text = result.get("next_collect_info", "")
    geometry_list = result.get("next_collect_geometry", [])

    # Clean lines from headers/separators
    lines = info_text.split("\n")
    relevant_lines = []
    for line in lines:
        strip = line.strip()
        if not strip.startswith("|") or strip.startswith("|   #"):
            continue
        if any(key in strip for key in ["Direction", "Path", "Row",
                                        "Mission", "Passes dates"]):
            continue
        relevant_lines.append(line)

    past_overpasses = []
    future_overpasses = []

    tf = TimezoneFinder()
    # Loop over lines and corresponding geometries
    for line, poly in zip(relevant_lines, geometry_list):
        if not isinstance(poly, Polygon):
            continue
        if not product_geom.intersects(poly):
            continue
        inter = product_geom.intersection(poly)
        if inter.is_empty:
            continue
        overlap_pct = (inter.area / product_geom.area) * 100.0

        # get inter time zone
        centroid = inter.centroid
        lon, lat = centroid.x, centroid.y
        timezone_name = tf.timezone_at(lat=lat, lng=lon)
        if timezone_name is None:
            # fallback if the polygon is offshore or ambiguous
            timezone_name = tf.closest_timezone_at(lat=lat, lng=lon)

        bbox_tz = ZoneInfo(timezone_name)

        # Extract datetimes
        if sat_name == 'Landsat':
            dt_strings = re.findall(r"\d{2}/\d{2}/\d{4}", line)
            dt_list = [
                datetime.strptime(dt_str, "%m/%d/%Y"
                                  ).replace(
                                    tzinfo=timezone.utc)
                for dt_str in dt_strings
            ]
        else:
            dt_strings = re.findall(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}",
                                    line)
            dt_list = [
                datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S"
                                  ).replace(
                                    tzinfo=timezone.utc)
                for dt_str in dt_strings
            ]

        # Keep only post-event
        dt_list = [dt for dt in dt_list if dt >= event_date]
        if not dt_list:
            continue

        # Orbit/Path info
        columns = [c.strip() for c in line.split("|")]
        if sat_name == "Landsat":
            if len(columns) >= 5:
                path = columns[2]
                row = columns[3]
                orbit_info = f"Path {path}, Row {row}"
            else:
                orbit_info = "Path/Row unknown"
        elif sat_name == "Sentinel-1":
            platform = columns[2]
            rel_orbit = columns[3]
            orbit_info = f"{platform}, Rel. orbit {rel_orbit}"
        else:
            # Sentinel-2
            orbit_info = f"Rel. orbit {columns[2]}"

        # Get current time in UTC then Split into past and future
        now_utc = datetime.now(timezone.utc)
        for dt in dt_list:
            # get local and event times from UTC time
            dt_local = dt.astimezone()
            dt_bbox = dt.astimezone(bbox_tz)

            if (sat_name == "Landsat"):
                entry = (f"{dt.strftime('%Y-%m-%d')} "
                         f": {orbit_info}, {overlap_pct:.1f}% overlap")
            else:
                utc_str = dt.strftime('%Y-%m-%d %H:%M:%S')
                local_str = dt_local.strftime('%Y-%m-%d %H:%M:%S')
                bbox_str = dt_bbox.strftime('%Y-%m-%d %H:%M:%S')
                local_tz_abbrev = dt_local.tzname()     # e.g., "PST"
                bbox_offset = dt_bbox.utcoffset()
                if bbox_offset is not None:
                    total_minutes = bbox_offset.total_seconds() / 60
                    hours = int(total_minutes // 60)
                    gmt_str = f"UTC{hours:+03d}"
                else:
                    gmt_str = ""
                entry = (
                    f"{utc_str} (UTC) "
                    f"| Operator TZ: {local_str} ({local_tz_abbrev}) "
                    f"| Event TZ: {bbox_str} ({gmt_str}) "
                    f": {orbit_info}, {overlap_pct:.1f}% overlap "
                    
                )
            if dt <= now_utc:
                past_overpasses.append((dt, entry))
            else:
                future_overpasses.append((dt, entry))

    # Check if we have no overlaps at all
    if not past_overpasses and not future_overpasses:
        return (f"No overlapping (with AOI) overpasses for "
                f"{sat_name} after {event_date.strftime(
                                    '%Y-%m-%d %H:%M:%S')}")

    # Sort past: oldest first, future: most recent first
    past_overpasses.sort(key=lambda x: x[0], reverse=False)
    future_overpasses.sort(key=lambda x: x[0], reverse=False)

    # Extract only the formatted strings for the report
    past_overpasses_str = [x[1] for x in past_overpasses]
    future_overpasses_str = [x[1] for x in future_overpasses]

    # Build formatted report
    report_lines = []
    if past_overpasses_str:
        report_lines.append(f"{sat_name} acquired data post-event on:")
        for entry in past_overpasses_str:
            report_lines.append(f"- {entry}")
        if future_overpasses_str:
            report_lines.append("and will acquire data on:")
            for entry in future_overpasses_str:
                report_lines.append(f"- {entry}")
    else:
        # No past overpasses
        if future_overpasses_str:
            report_lines.append(f"{sat_name} will acquire data on:")
            for entry in future_overpasses_str:
                report_lines.append(f"- {entry}")
        else:
            report_lines.append("No overlapping overpasses available.")

    return "\n".join(report_lines)
