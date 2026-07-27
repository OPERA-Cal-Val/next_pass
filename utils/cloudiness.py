import json
import logging
import os
import random
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union

import numpy as np
import rasterio
import requests
from dateutil.parser import parse as parse_datetime
from requests.adapters import HTTPAdapter
from shapely.geometry import Point, Polygon, mapping, shape

LOGGER = logging.getLogger(__name__)

hit_api_limit: bool = False

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "cloudiness-client/1.0"})

adapter = HTTPAdapter(pool_connections=4, pool_maxsize=4)
SESSION.mount("https://", adapter)


def chunks(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i : i + size]


def as_utc_datetime(dt_like: Union[str, datetime]) -> datetime:
    """Normalize string/datetime input to a timezone-aware UTC datetime."""
    dt = parse_datetime(dt_like) if isinstance(dt_like, str) else dt_like
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class RateLimiter:
    def __init__(self, rate_per_sec):
        self.lock = threading.Lock()
        self.delay = 1 / rate_per_sec
        self.last_time = 0

    def wait(self):
        with self.lock:
            now = time.time()
            wait_time = self.delay - (now - self.last_time)
            if wait_time > 0:
                time.sleep(wait_time)
            self.last_time = time.time()


def api_limit_reached() -> bool:
    """
    Returns True if the weather API daily limit has been reached.
    """
    global hit_api_limit
    return hit_api_limit


def get_cloudiness(url):
    """Download a CLOUD*.tif file and calculate cloud pixel percentage."""
    cloud_values = {4, 5, 6, 7, 12, 13, 14, 15}
    exclude_values = {255}

    try:
        with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
            response = requests.get(url, stream=True)
            if response.status_code != 200:
                LOGGER.warning(
                    "Failed to download %s (status %s)", url, response.status_code
                )
                return None

            for chunk in response.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp_path = tmp.name

        with rasterio.open(tmp_path) as src:
            band = src.read(1)

        os.remove(tmp_path)

        # Create mask of valid pixels (exclude 255, 8)
        valid_mask = ~np.isin(band, list(exclude_values))
        if not np.any(valid_mask):
            return None

        # Count cloud-affected pixels
        cloud_mask = np.isin(band, list(cloud_values))
        cloud_affected_pixels = np.count_nonzero(cloud_mask & valid_mask)
        total_valid_pixels = np.count_nonzero(valid_mask)

        area_km2 = 0.03 * 0.03 * total_valid_pixels
        cloud_percent = (cloud_affected_pixels / total_valid_pixels) * 100

        return round(cloud_percent, 2), round(area_km2, 2)

    except Exception as e:  # noqa: BLE001
        LOGGER.error("Error processing %s: %s", url, e)
        return None


def generate_random_sample_points(polygon: Polygon, n: int = 10) -> List[Point]:
    """Generate random sample points within a polygon."""
    minx, miny, maxx, maxy = polygon.bounds
    points: List[Point] = []
    attempts = 0
    max_attempts = n * 10  # avoid infinite loop if polygon is small

    while len(points) < n and attempts < max_attempts:
        x = random.uniform(minx, maxx)
        y = random.uniform(miny, maxy)
        p = Point(x, y)
        if polygon.contains(p):
            points.append(p)
        attempts += 1

    return points


def generate_grid_sample_points(polygon: Polygon, num_points: int = 10) -> List[Point]:
    """
    Generate approximately `num_points' evenly spaced points within a polygon
    """
    minx, miny, maxx, maxy = polygon.bounds
    area = polygon.area

    # Estimate grid spacing based on area and number of points
    grid_spacing = (area / num_points) ** 0.5

    # Create grid points
    x_coords = np.arange(minx, maxx, grid_spacing)
    y_coords = np.arange(miny, maxy, grid_spacing)

    points: List[Point] = []
    for x in x_coords:
        for y in y_coords:
            p = Point(x, y)
            if polygon.contains(p):
                points.append(p)

    return points


def get_cloudiness_at_point(
    lat: float,
    lon: float,
    target_iso: str,
    allow_nearest: bool = False,
    session: requests.Session = SESSION,
) -> Optional[float]:
    """
    Get cloudiness forecast at a single point and time.

    Parameters
    ----------
    lat, lon : float
        Coordinates of the point of interest.
    target_iso : str
        Target datetime in ISO format (e.g. '2025-08-28T15:00') in UTC.
    allow_nearest : bool, optional
        If True, will return cloudiness for the nearest available time
        if exact time is not found.
    session: requests.Session, optional

    Returns
    -------
    cloudiness : float or None
        Cloud cover in %, or None if not available or on API error.
    """
    url = "https://api.open-meteo.com/v1/forecast"

    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "cloudcover",
        "timezone": "UTC",
    }
    try:
        response = session.get(url, params=params, timeout=10)
        response.raise_for_status()
        hourly = response.json().get("hourly", {})

        times = hourly.get("time", [])
        clouds = hourly.get("cloudcover", [])

        if not times or not clouds or len(times) != len(clouds):
            return None

        # Try exact match
        if target_iso in times:
            return clouds[times.index(target_iso)]

        # If not found, find closest time (optional)
        if allow_nearest:
            target_dt_obj = parse_datetime(target_iso)
            time_diffs = [
                abs((parse_datetime(t) - target_dt_obj).total_seconds()) for t in times
            ]
            min_idx = time_diffs.index(min(time_diffs))
            return clouds[min_idx]

        return None

    except (requests.RequestException, KeyError, ValueError) as e:
        LOGGER.error("Error calculating cloudiness using %s: %s", url, e)
        return None


def get_cloudiness_at_points(
    points: list[tuple[float, float]],
    target_iso: str,
    allow_nearest: bool = False,
    session: requests.Session = SESSION,
) -> list[Optional[float]]:
    """
    Same logic as get_cloudiness_at_point, but for multiple points
    (instead of just one) at a given time using a single Open-Meteo API call.
    """
    global hit_api_limit

    if hit_api_limit:
        LOGGER.warning("Weather API limit already reached. Skipping request.")
        return [None] * len(points)

    url = "https://api.open-meteo.com/v1/forecast"

    latitudes = ",".join(str(lat) for lat, _ in points)
    longitudes = ",".join(str(lon) for _, lon in points)

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "hourly": "cloudcover",
        "timezone": "UTC",
    }

    try:
        response = session.get(url, params=params, timeout=10)
        response.raise_for_status()

        # to avoid 'list' object has no attribute 'get' error message
        data = response.json()
        if isinstance(data, list):
            hourlies = [d.get("hourly", {}) for d in data if isinstance(d, dict)]
        else:
            # to be safe we consider single point possibilty
            hourlies = [data.get("hourly", {})]

        results: list[Optional[float]] = []
        for hourly in hourlies:  # loop over each point's data

            times = hourly.get("time", [])
            clouds = hourly.get("cloudcover", [])

            if not times or not clouds or len(times) != len(clouds):
                results.append(None)
                continue

            # Exact match
            if target_iso in times:
                results.append(clouds[times.index(target_iso)])
                continue

            # Nearest match
            if allow_nearest:
                target_dt_obj = parse_datetime(target_iso)
                time_diffs = [
                    abs((parse_datetime(t) - target_dt_obj).total_seconds())
                    for t in times
                ]
                min_idx = time_diffs.index(min(time_diffs))
                results.append(clouds[min_idx])
                continue

            results.append(None)

        return results

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            hit_api_limit = True
            try:
                error_info = json.loads(e.response.text)
                reason = error_info.get("reason", "Unknown 429 error")
            except Exception:
                reason = e.response.text
            LOGGER.warning(f"{reason}")
            return [None] * len(points)
        else:
            LOGGER.error(
                "HTTP error calculating historical cloudiness using %s: %s", url, e
            )
            return [None] * len(points)

    except (requests.RequestException, KeyError, ValueError) as e:
        LOGGER.error("Error calculating historical cloudiness using %s: %s", url, e)
        return [None] * len(points)


def get_historical_cloudiness_at_point(
    lat: float,
    lon: float,
    target_iso: str,
    allow_nearest: bool = False,
    session: requests.Session = SESSION,
) -> Optional[float]:
    """
    Get historical cloudiness at a single point and time.

    Parameters
    ----------
    lat, lon : float
        Coordinates of the point of interest.
    target_iso : str
        Target datetime in ISO format (e.g. '2025-08-28T15:00') in UTC.
    allow_nearest : bool, optional
        If True, will return cloudiness for the nearest available time
        if exact time is not found.
    session: requests.Session, optional

    Returns
    -------
    cloudiness : float or None
        Cloud cover in %, or None if not available or on API error.
    """
    target_dt = parse_datetime(target_iso)
    target_date_str = target_dt.strftime("%Y-%m-%d")

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": target_date_str,
        "end_date": target_date_str,
        "hourly": "cloudcover",
        "timezone": "UTC",
    }

    try:
        response = session.get(url, params=params, timeout=10)
        response.raise_for_status()
        hourly = response.json().get("hourly", {})

        times = hourly.get("time", [])
        clouds = hourly.get("cloudcover", [])

        if not times or not clouds or len(times) != len(clouds):
            return None

        # Try exact match
        if target_iso in times:
            return clouds[times.index(target_iso)]

        # Find nearest match (optional)
        if allow_nearest:
            time_diffs = [
                abs((parse_datetime(t) - target_dt).total_seconds()) for t in times
            ]
            min_idx = time_diffs.index(min(time_diffs))
            return clouds[min_idx]

        return None

    except (requests.RequestException, KeyError, ValueError) as e:
        LOGGER.error("Error calculating historical cloudiness using %s: %s", url, e)
        return None


def get_historical_cloudiness_at_points(
    points: list[tuple[float, float]],
    target_iso: str,
    allow_nearest: bool = False,
    session: requests.Session = SESSION,
) -> list[Optional[float]]:
    """
    Get historical cloudiness for multiple points at a given time
    using a single Open-Meteo archive API call.
    """
    global hit_api_limit
    if hit_api_limit:
        LOGGER.warning("Weather API limit already reached. Skipping request.")
        return [None] * len(points)

    target_dt = parse_datetime(target_iso)
    target_date_str = target_dt.strftime("%Y-%m-%d")

    url = "https://archive-api.open-meteo.com/v1/archive"

    latitudes = ",".join(str(lat) for lat, _ in points)
    longitudes = ",".join(str(lon) for _, lon in points)

    params = {
        "latitude": latitudes,
        "longitude": longitudes,
        "start_date": target_date_str,
        "end_date": target_date_str,
        "hourly": "cloudcover",
        "timezone": "UTC",
    }

    try:
        response = session.get(url, params=params, timeout=10)
        response.raise_for_status()

        data = response.json()
        if isinstance(data, list):
            hourlies = [d.get("hourly", {}) for d in data if isinstance(d, dict)]
        else:
            hourlies = [data.get("hourly", {})]

        results: list[Optional[float]] = []
        for hourly in hourlies:  # loop over each point's data
            times = hourly.get("time", [])
            clouds = hourly.get("cloudcover", [])

            if not times or not clouds or len(times) != len(clouds):
                results.append(None)
                continue

            # Exact match
            if target_iso in times:
                results.append(clouds[times.index(target_iso)])
                continue

            # Nearest match
            if allow_nearest:
                time_diffs = [
                    abs((parse_datetime(t) - target_dt).total_seconds()) for t in times
                ]
                min_idx = time_diffs.index(min(time_diffs))
                results.append(clouds[min_idx])
                continue

            results.append(None)

        return results

    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 429:
            hit_api_limit = True
            remaining = e.response.headers.get("X-RateLimit-Remaining")
            reset = e.response.headers.get("X-RateLimit-Reset")
            LOGGER.warning("API limit hit: remaining=%s reset=%s", remaining, reset)
            return [None] * len(points)
        else:
            LOGGER.error(
                "HTTP error calculating historical cloudiness using %s: %s", url, e
            )
            return [None] * len(points)

    except (requests.RequestException, KeyError, ValueError) as e:
        LOGGER.error("Error calculating historical cloudiness using %s: %s", url, e)
        return [None] * len(points)


def get_overpass_cloudiness(
    polygon_geojson: Dict,
    target_datetime: Union[str, datetime],
    num_samples: int = 10,
    allow_nearest: bool = False,
    sampling_method: str = "random",  # "random" or "grid"
) -> Optional[float]:
    """
    Get forecasted or historical average cloudiness over a polygon area,
    depending on if the date is in the past or in the future.

    Parameters
    ----------
    polygon_geojson : dict
        GeoJSON polygon.
    target_datetime : str or datetime
        Datetime string or datetime object (UTC).
    num_samples : int
        Number of sample points inside the polygon.
    allow_nearest : bool
        Use nearest forecast time if exact not found.

    Returns
    -------
    float or None
        Average cloudiness over polygon at specified time.
    """
    try:
        # Normalize datetime
        target_dt_utc = as_utc_datetime(target_datetime)
        target_iso = target_dt_utc.strftime("%Y-%m-%dT%H:%M")

        # Prepare polygon and sample points
        poly = shape(polygon_geojson)
        if not poly.is_valid:
            poly = poly.buffer(0)

        if sampling_method == "grid":
            points = generate_grid_sample_points(poly, num_points=num_samples)
        else:
            points = generate_random_sample_points(poly, n=num_samples)

        if not points:
            return None

        global hit_api_limit
        cloudiness_values: List[float] = []

        is_future = target_dt_utc > datetime.now(timezone.utc)
        batch_func = (
            get_cloudiness_at_points
            if is_future
            else get_historical_cloudiness_at_points
        )
        cloudiness_values: List[float] = []
        max_workers = 4  # Limit to 4 to avoid API rate limits
        BATCH_SIZE = 20  # Number of points per batch
        rate_limiter = RateLimiter(rate_per_sec=3)  # max 3 requests/sec

        if hit_api_limit:
            LOGGER.warning("Weather API limit already reached. Skipping requests !")
            return None

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            cloud_vals = []
            for batch in chunks(points, BATCH_SIZE):
                if hit_api_limit:
                    break  # stop processing further batches
                rate_limiter.wait()
                try:
                    cloud_vals.append(
                        executor.submit(
                            batch_func,
                            [(pt.y, pt.x) for pt in batch],
                            target_iso,
                            allow_nearest,
                        )
                    )
                except requests.exceptions.HTTPError as e:
                    if e.response.status_code == 429:
                        LOGGER.warning(
                            "Weather API limit reached. Stopping further requests."
                        )
                        hit_api_limit = True
                        break
                    else:
                        raise e

            for cloud_val in as_completed(cloud_vals):
                val = cloud_val.result()
                if val is None:
                    continue
                if isinstance(val, list):
                    cloudiness_values.extend(val)
                else:
                    cloudiness_values.append(val)

        valid_values = [v for v in cloudiness_values if v is not None]

        if not valid_values:
            return None  # avoids ZeroDivisionError

        return sum(valid_values) / len(valid_values)

    except Exception as e:  # noqa: BLE001
        LOGGER.error("Error predict_cloudiness: %s", e)
        return None


def make_get_cloudiness_for_row(aoi_polygon: Polygon):
    """
    Return a function that computes cloudiness for each row in a GeoDataFrame.
    """

    def get_cloudiness_for_row(row):
        # Check if we have a list of timestamps
        timestamps = (
            row.begin_date if isinstance(row.begin_date, list) else [row.begin_date]
        )
        cloudiness_vals: List[Optional[float]] = []

        for timestamp in timestamps:
            now = datetime.now(timezone.utc)
            four_days_later = now + timedelta(days=4)
            fourteen_days_later = now + timedelta(days=14)

            intersection_geom = row.geometry.intersection(aoi_polygon)

            if intersection_geom.is_empty:
                cloudiness_vals.append(None)
                continue

            geojson_geom = mapping(intersection_geom)

            n_samples = 210 if now <= timestamp <= four_days_later else 60

            if timestamp <= fourteen_days_later:
                try:
                    cloudiness = get_overpass_cloudiness(
                        polygon_geojson=geojson_geom,
                        target_datetime=timestamp,
                        num_samples=n_samples,
                        allow_nearest=True,
                        sampling_method="grid",
                    )
                    cloudiness_vals.append(cloudiness)
                except Exception as e:
                    LOGGER.warning(
                        "Cloudiness prediction failed for %s: %s",
                        timestamp,
                        e,
                    )
                    cloudiness_vals.append(None)
            else:
                cloudiness_vals.append(None)

        return cloudiness_vals

    return get_cloudiness_for_row
