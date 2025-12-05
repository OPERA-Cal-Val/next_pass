import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List

import geopandas as gpd
import pandas as pd

from utils.utils import download_kml, parse_kml

SCRATCH_DIR = Path.cwd() / "scratch"


def sync_scratch_directory(
    urls: List[str],
    mission_name: str,
    scratch_dir: Path,
    logger: logging.Logger,
) -> List[Path]:
    """
    Synchronize local scratch directory with online ESA URLs.

    Downloads missing files and removes obsolete files.

    Args:
        urls (List[str]): List of ESA download URLs.
        mission_name (str): Mission prefix (e.g., sentinel1, sentinel2).
        scratch_dir (Path): Local scratch directory.
        logger (logging.Logger): Logger for status updates.

    Returns:
        List[Path]: List of local KML file paths that match online URLs.
    """
    scratch_dir.mkdir(exist_ok=True)

    # Extract expected filenames from URLs
    expected_kml_names = {f"{mission_name}_{Path(url).stem}.kml" for url in urls}

    # Find existing KML files in scratch
    existing_kml_files = {p.name for p in scratch_dir.glob(f"{mission_name}*.kml")}

    # Determine missing and obsolete files
    missing_files = expected_kml_names - existing_kml_files
    obsolete_files = existing_kml_files - expected_kml_names

    # Delete obsolete files
    for file_name in obsolete_files:
        file_path = scratch_dir / file_name
        try:
            file_path.unlink()
            logger.info("Deleted obsolete file: %s", file_path)
        except Exception as e:
            logger.error("Failed to delete %s: %s", file_path, e)

    # Download missing files
    local_kml_paths: List[Path] = []
    for url in urls:
        filename = f"{mission_name}_{Path(url).stem}.kml"
        file_path = scratch_dir / filename

        if file_path.name in missing_files or not file_path.exists():
            try:
                download_kml(url, str(file_path))
            except Exception as e:
                logger.error("Failed downloading %s: %s", url, e)
                continue

        local_kml_paths.append(file_path)

    return local_kml_paths


def build_sentinel_collection(
    urls: List[str],
    n_day_past: float,
    mission_name: str,
    out_filename: str,
    logger: logging.Logger,
    platforms: list | None = None,
) -> Path:
    """
    Download, parse, and merge Sentinel acquisition plans into a GeoJSON file.

    Args:
        urls (List[str]): List of ESA download URLs.
        mission_name (str): Name prefix for output filenames.
        out_filename (str): Final GeoJSON output filename.
        logger (logging.Logger): Logger object for status reporting.

    Returns:
        Path: Path to the generated GeoJSON file.
    """
    out_path = SCRATCH_DIR / out_filename
    SCRATCH_DIR.mkdir(exist_ok=True)

    # Sync scratch directory with online files
    local_kml_paths = sync_scratch_directory(urls, mission_name, SCRATCH_DIR, logger)

    # Build platform mapping if platforms list is provided
    platform_by_name: dict[str, str] = {}
    if platforms:
        platform_by_name = {Path(u).stem.lower(): p for u, p in zip(urls, platforms)}

    gdfs: list[gpd.GeoDataFrame] = []

    for kml_path in local_kml_paths:
        collection_path = SCRATCH_DIR / f"{kml_path.stem}.geojson"
        platform = None

        if platform_by_name:
            stem = kml_path.stem.lower()
            # first attempt: direct match
            platform = platform_by_name.get(stem)

            # second attempt: drop leading token
            if platform is None and "_" in stem:
                stem_id = "_".join(stem.split("_")[1:])
                platform = platform_by_name.get(stem_id)

            # last resort: partial match
            if platform is None:
                for key, value in platform_by_name.items():
                    if key in stem:
                        platform = value
                        break

        if collection_path.exists():
            logger.info("Using cached file: %s", collection_path)
            try:
                gdf = gpd.read_file(collection_path)
            except Exception as e:
                logger.error("Failed reading %s: %s", collection_path, e)
                continue
        else:
            logger.info("Parsing new file: %s", kml_path)
            try:
                gdf = parse_kml(kml_path)
                if not gdf.empty:
                    gdf.to_file(collection_path)
                else:
                    logger.warning("No valid data in file: %s", kml_path)
                    continue
            except Exception as e:
                logger.error("Failed parsing %s: %s", kml_path, e)
                continue

        gdf["platform"] = platform
        gdfs.append(gdf)

    if not gdfs:
        logger.error("No valid GeoDataFrames created.")
        return Path()

    n_days_earlier = datetime.now(timezone.utc) - timedelta(days=n_day_past)

    full_gdf = pd.concat(gdfs).drop_duplicates()
    full_gdf["begin_date"] = pd.to_datetime(full_gdf["begin_date"], utc=True)
    full_gdf = full_gdf.loc[full_gdf["begin_date"] >= n_days_earlier]
    full_gdf = full_gdf.sort_values("begin_date").reset_index(drop=True)

    try:
        full_gdf.to_file(out_path)
        logger.info("%s collection saved to: %s", mission_name, out_path)
    except Exception as e:
        logger.error("Failed to write final output file: %s", e)
        return Path()

    return out_path
