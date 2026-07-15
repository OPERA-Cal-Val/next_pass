"""
NOAA Tide Prediction Module

CRITICAL TIMEZONE CONTRACT:
===========================
All datetimes in this module are NAIVE datetimes representing UTC/GMT.

- parse_datetime() returns naive datetimes (UTC)
- NOAA API is always called with time_zone="gmt"
- All comparisons assume naive datetimes are UTC
- DO NOT pass timezone-aware datetimes to functions in this module
- DO NOT change NOAA API time_zone parameter

This design is intentional for NOAA API compatibility.
See claude_md/timezone_handling.md for full explanation.

DEVELOPER GUIDELINES:
=====================
If you modify this code:

1. ✅ ALWAYS keep all datetimes as naive representing UTC
2. ✅ ALWAYS configure NOAA API with time_zone="gmt"
3. ✅ NEVER pass timezone-aware datetimes to tide prediction functions
4. ✅ NEVER change NOAA API time_zone parameter

Breaking these rules will cause TypeError in datetime comparisons.
"""

import requests
import os
import time
import json
import logging
import math
from pathlib import Path
from typing import Optional
from datetime import datetime, timedelta
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points

from utils.utils import TIDE_PREDICTION_WINDOW_DAYS

LOGGER = logging.getLogger("tide_prediction")

NOAA_URL = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"
STATIONS_URL = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
_STATIONS_CACHE = None
TIDE_DIRECTION_UNKNOWN = "slack"
SCRATCH_DIR = Path.cwd() / "scratch"
MAX_NEARBY_STATION_DISTANCE_KM = 50.0


def resolve_station_cache_path(filepath: str | Path | None = None) -> Path:
    """Return the cache path for NOAA station metadata."""
    return Path(filepath) if filepath is not None else SCRATCH_DIR / "noaa_stations.json"


def get_stations(filepath: str | Path | None = None):
    global _STATIONS_CACHE
    if _STATIONS_CACHE is None:
        _STATIONS_CACHE = load_stations(filepath)
    return _STATIONS_CACHE


def cache_stations(filepath: str | Path | None = None):
    global _STATIONS_CACHE
    cache_path = resolve_station_cache_path(filepath)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(STATIONS_URL, timeout=10)
    response.raise_for_status()

    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(response.json(), f)

    _STATIONS_CACHE = None


def load_stations(filepath: str | Path | None = None):
    cache_path = resolve_station_cache_path(filepath)
    with open(cache_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("stations", [])


def ensure_station_cache(filepath: str | Path | None = None, max_age_days=30):
    cache_path = resolve_station_cache_path(filepath)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if not cache_path.exists():
        cache_stations(cache_path)
        return

    age_days = (time.time() - os.path.getmtime(cache_path)) / 86400
    if age_days > max_age_days:
        cache_stations(cache_path)


def parse_datetime(dt_str: str) -> datetime:
    """Parse ISO datetime string into naive datetime representing UTC.

    IMPORTANT: Returns naive datetime. All timestamps in this module
    are naive datetimes representing UTC/GMT. NOAA API is always
    configured with time_zone="gmt".

    Do not mix with timezone-aware datetimes - will cause TypeError.

    Args:
        dt_str: ISO datetime string (e.g., "2026-06-28T18:03:59" or "2026-06-28 18:00")

    Returns:
        Naive datetime object representing UTC time
    """
    return datetime.fromisoformat(dt_str.replace("Z", ""))


def _build_station_record(station: dict) -> dict:
    return {
        "id": station["id"],
        "name": station.get("name", station["id"]),
        "lat": float(station["lat"]),
        "lng": float(station["lng"]),
    }


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    earth_radius_km = 6371.0
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    sin_lat = math.sin(delta_lat / 2)
    sin_lon = math.sin(delta_lon / 2)
    a = (
        sin_lat * sin_lat
        + math.cos(lat1_rad) * math.cos(lat2_rad) * sin_lon * sin_lon
    )
    return 2 * earth_radius_km * math.asin(math.sqrt(a))


def _station_distance_km_to_geometry(
    geometry: BaseGeometry,
    station_lat: float,
    station_lon: float,
) -> float:
    station_point = Point(station_lon, station_lat)
    nearest_geom_point, _ = nearest_points(geometry, station_point)
    return _haversine_km(
        nearest_geom_point.y,
        nearest_geom_point.x,
        station_lat,
        station_lon,
    )


def get_stations_in_aoi(
    polygon: BaseGeometry,
    max_stations: int = 3,
    max_distance_km: float = MAX_NEARBY_STATION_DISTANCE_KM,
) -> list:
    """Return relevant NOAA stations for the AOI.

    For polygon AOIs, return stations inside the AOI. If none are inside,
    fall back to the nearest stations within ``max_distance_km`` of the AOI.
    For point AOIs, return up to ``max_stations`` nearest stations within
    ``max_distance_km`` of the point.
    """
    ensure_station_cache()
    stations = get_stations()

    if polygon.geom_type != "Point":
        inside = []
        for st in stations:
            lat = st.get("lat")
            lon = st.get("lng")
            if lat is None or lon is None:
                continue
            if polygon.contains(Point(float(lon), float(lat))):
                inside.append(_build_station_record(st))
        if inside:
            return inside

    nearby = []
    for st in stations:
        lat = st.get("lat")
        lon = st.get("lng")
        if lat is None or lon is None:
            continue
        lat_f = float(lat)
        lon_f = float(lon)
        distance_km = _station_distance_km_to_geometry(polygon, lat_f, lon_f)
        if distance_km <= max_distance_km:
            nearby.append((distance_km, _build_station_record(st)))

    nearby.sort(key=lambda item: item[0])
    return [station for _, station in nearby[:max_stations]]


def interpolate_tide(times, values, target_dt):
    """Interpolate tide height at target time.

    WARNING: All inputs must be naive datetimes representing UTC.
    Do not mix timezone-aware and naive datetimes.

    Args:
        times: List of ISO datetime strings
        values: List of tide heights in meters
        target_dt: Naive datetime representing UTC

    Returns:
        Interpolated tide height in meters, or None if outside time range

    Raises:
        TypeError: If target_dt is timezone-aware
    """
    # Safety check: reject timezone-aware datetimes
    if hasattr(target_dt, 'tzinfo') and target_dt.tzinfo is not None:
        raise TypeError(
            f"interpolate_tide expects naive datetime (UTC), got timezone-aware: {target_dt.tzinfo}. "
            f"See module docstring for timezone contract."
        )

    for i in range(len(times) - 1):
        t1 = parse_datetime(times[i])
        t2 = parse_datetime(times[i + 1])

        if t1 <= target_dt <= t2:
            v1 = values[i]
            v2 = values[i + 1]

            fraction = (
                (target_dt - t1).total_seconds()
                / (t2 - t1).total_seconds()
            )
            return v1 + fraction * (v2 - v1)

    return None



def _find_tide_direction(times_iso: list, values: list, target_dt: datetime) -> str:
    """Return 'rising', 'falling', or 'slack' based on hourly values bracketing target_dt.

    Calculates the rate of change (slope) in meters per hour and compares to a threshold
    of 0.02 m/hr to distinguish active tide movement from slack water.

    WARNING: target_dt must be naive datetime representing UTC.

    Args:
        times_iso: List of ISO datetime strings
        values: List of tide heights in meters
        target_dt: Naive datetime representing UTC

    Returns:
        'rising', 'falling', or 'slack' (TIDE_DIRECTION_UNKNOWN)

    Raises:
        TypeError: If target_dt is timezone-aware
    """
    # Safety check: reject timezone-aware datetimes
    if hasattr(target_dt, 'tzinfo') and target_dt.tzinfo is not None:
        raise TypeError(
            f"_find_tide_direction expects naive datetime (UTC), got timezone-aware: {target_dt.tzinfo}. "
            f"See module docstring for timezone contract."
        )

    before_val = before_t = None
    after_val = after_t = None

    for i, t_str in enumerate(times_iso):
        t = parse_datetime(t_str)
        if t <= target_dt:
            if before_t is None or t > before_t:
                before_t, before_val = t, values[i]
        else:
            if after_t is None or t < after_t:
                after_t, after_val = t, values[i]

    if before_val is not None and after_val is not None and before_t is not None and after_t is not None:
        # Calculate slope (rate of change) in meters per hour
        time_diff_hours = (after_t - before_t).total_seconds() / 3600.0
        if time_diff_hours > 0:
            slope = (after_val - before_val) / time_diff_hours

            # Apply threshold to distinguish active movement from slack water
            THRESHOLD_M_PER_HOUR = 0.02
            if slope > THRESHOLD_M_PER_HOUR:
                return "rising"
            elif slope < -THRESHOLD_M_PER_HOUR:
                return "falling"
            else:
                return TIDE_DIRECTION_UNKNOWN  # "slack"

    return ""


def _find_nearest_hilo_label(hilo_predictions: list, target_dt: datetime) -> str:
    """Return the type (H, HH, L, LL) of the hilo event nearest to target_dt.

    WARNING: target_dt must be naive datetime representing UTC.

    Args:
        hilo_predictions: List of dicts with 't' (timestamp) and 'type' (H/HH/L/LL)
        target_dt: Naive datetime representing UTC

    Returns:
        'H', 'HH', 'L', 'LL', or '' if no predictions

    Raises:
        TypeError: If target_dt is timezone-aware
    """
    # Safety check: reject timezone-aware datetimes
    if hasattr(target_dt, 'tzinfo') and target_dt.tzinfo is not None:
        raise TypeError(
            f"_find_nearest_hilo_label expects naive datetime (UTC), got timezone-aware: {target_dt.tzinfo}. "
            f"See module docstring for timezone contract."
        )

    if not hilo_predictions:
        return ""
    nearest_label = ""
    min_diff = None
    for p in hilo_predictions:
        t = parse_datetime(p["t"].replace(" ", "T"))
        diff = abs((t - target_dt).total_seconds())
        if min_diff is None or diff < min_diff:
            min_diff = diff
            nearest_label = p.get("type", "")
    return nearest_label


def get_tide_info_batch(
    polygon: BaseGeometry,
    target_isos: list,
    station_dicts: list,
    allow_interpolation: bool = True,
    session: Optional[requests.Session] = None,
) -> list:
    """Return a dict per target time with 'nearest' (table) and 'per_station' (map) values.

    Args:
        polygon: AOI geometry (used only for finding centroid to determine "nearest" station)
        target_isos: List of ISO datetime strings for tide predictions
        station_dicts: List of station dicts from get_stations_in_aoi()
        allow_interpolation: Whether to interpolate tide values between hourly predictions
        session: Optional requests.Session for connection pooling

    Returns:
        List of dicts with 'nearest' and 'per_station' tide data, or None for missing data
    """

    if session is None:
        session = requests.Session()

    try:
        if not station_dicts:
            LOGGER.warning("No stations provided")
            return [None] * len(target_isos)

        # Find nearest station to polygon centroid
        centroid = polygon.centroid
        nearest_id = min(
            station_dicts,
            key=lambda st: (st["lat"] - centroid.y) ** 2 + (st["lng"] - centroid.x) ** 2,
        )["id"]

        target_dts = [parse_datetime(t) for t in target_isos]

        # Filter out dates more than TIDE_PREDICTION_WINDOW_DAYS in the future
        # (NOAA API limit). Keep all past dates.
        now = datetime.now()
        max_future_date = now + timedelta(days=TIDE_PREDICTION_WINDOW_DAYS)

        # Track which indices are beyond the 2-month limit
        valid_indices = []
        valid_dts = []
        for i, dt in enumerate(target_dts):
            if dt <= max_future_date:
                valid_indices.append(i)
                valid_dts.append(dt)

        # If no valid dates, return all None
        if not valid_dts:
            LOGGER.warning("All requested dates are more than 2 months in the future - skipping tide predictions")
            return [None] * len(target_isos)

        # Use only valid dates for API request
        begin_date = (min(valid_dts) - timedelta(days=1)).strftime("%Y%m%d")
        end_date = (max(valid_dts) + timedelta(days=1)).strftime("%Y%m%d")

        # per_station_results[i] = list of (station_id, formatted_string)
        # Only initialize for valid indices (within 2-month limit)
        per_station_results = {i: [] for i in valid_indices}
        nearest_results = {i: None for i in valid_indices}

        for st in station_dicts:
            station_id = st["id"]
            base_params = {
                "application": "tide_app",
                "begin_date": begin_date,
                "end_date": end_date,
                "datum": "MLLW",
                "station": station_id,
                "time_zone": "gmt",
                "units": "metric",
                "format": "json",
            }

            try:
                hourly_resp = session.get(
                    NOAA_URL, params={**base_params, "product": "predictions", "interval": "h"}, timeout=10
                )
                hourly_resp.raise_for_status()
                predictions = hourly_resp.json().get("predictions", [])

                hilo_resp = session.get(
                    NOAA_URL, params={**base_params, "product": "predictions", "interval": "hilo"}, timeout=10
                )
                hilo_resp.raise_for_status()
                hilo_predictions = hilo_resp.json().get("predictions", [])

                if not predictions:
                    continue

                times_iso = [p["t"].replace(" ", "T") for p in predictions]
                values = [float(p["v"]) for p in predictions]
                times_iso_short = [t[:16] for t in times_iso]

                # Only process valid indices (within 2-month limit)
                for j, valid_idx in enumerate(valid_indices):
                    target_iso = target_isos[valid_idx]
                    target_dt = valid_dts[j]
                    target_iso_short = target_iso[:16]
                    if target_iso_short in times_iso_short:
                        value = values[times_iso_short.index(target_iso_short)]
                    elif allow_interpolation:
                        value = interpolate_tide(times_iso, values, target_dt)
                        if value is None:
                            diffs = [abs((parse_datetime(t) - target_dt).total_seconds()) for t in times_iso]
                            value = values[diffs.index(min(diffs))]
                    else:
                        diffs = [abs((parse_datetime(t) - target_dt).total_seconds()) for t in times_iso]
                        value = values[diffs.index(min(diffs))]

                    label = _find_nearest_hilo_label(hilo_predictions, target_dt)
                    direction = _find_tide_direction(times_iso, values, target_dt)
                    tag = "-".join(filter(None, [label, direction]))
                    label_str = f"({tag})" if tag else ""
                    formatted = f"{value:.2f}{label_str}"

                    per_station_results[valid_idx].append((station_id, formatted))
                    if station_id == nearest_id:
                        nearest_results[valid_idx] = formatted

            except requests.RequestException:
                continue

        results = []
        for i in range(len(target_isos)):
            # Skip indices beyond 2-month limit (not in valid_indices)
            per = per_station_results.get(i, [])
            if not per:
                results.append(None)
                continue
            nearest = nearest_results.get(i) or per[0][1]
            results.append({"nearest": nearest, "per_station": {sid: v for sid, v in per}})

        return results

    except (requests.RequestException, KeyError, ValueError, OSError) as e:
        LOGGER.error("Error retrieving tide data from NOAA: %s", e)
        return [None] * len(target_isos)


def make_get_tide_for_row(aoi_geometry, station_dicts):
    """Create a function to get tide info for each overpass row.

    Args:
        aoi_geometry: Full AOI geometry (used for finding centroid)
        station_dicts: List of station dicts from get_stations_in_aoi() - reused for all overpasses

    Returns:
        Function that takes a dataframe row and returns tide info
    """
    def get_tide_for_row(row):
        from datetime import timezone

        times = row["begin_date"]

        if not isinstance(times, list):
            times = [times]

        target_isos = []
        for t in times:
            if isinstance(t, datetime):
                # Ensure datetime is in UTC before converting to naive string
                # This prevents silent errors if a non-UTC datetime somehow enters the system
                if t.tzinfo is not None and t.tzinfo != timezone.utc:
                    LOGGER.warning(
                        "Non-UTC datetime detected (%s), converting to UTC for tide prediction",
                        t.tzinfo
                    )
                    t = t.astimezone(timezone.utc)
                elif t.tzinfo is None:
                    LOGGER.warning(
                        "Naive datetime detected (no timezone), assuming UTC for tide prediction"
                    )
                # Convert to naive ISO string (timezone stripped intentionally)
                # NOAA API is configured with time_zone="gmt" to interpret these as UTC
                t = t.strftime("%Y-%m-%dT%H:%M:%S")
            target_isos.append(t)

        return get_tide_info_batch(
            polygon=aoi_geometry,
            target_isos=target_isos,
            station_dicts=station_dicts,
            allow_interpolation=True,
        )

    return get_tide_for_row
