import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from next_pass.utils import download_kml, parse_kml

SCRATCH_DIR = Path.cwd() / "scratch"

DEFAULT_MAX_WORKERS = 8

# Sentinels for _load_kml's outcome. Plain objects (not strings) so an `is`
# check never risks tripping a GeoDataFrame's ambiguous `==` truth value.
_PARSE_EMPTY = object()
_PARSE_FAILED = object()


def _resolve_max_workers(num_tasks: int) -> int:
    """Resolve the thread-pool size, bounded by the number of tasks.

    Configurable via the ``NEXTPASS_MAX_WORKERS`` environment variable; falls
    back to ``DEFAULT_MAX_WORKERS`` if unset or not a positive integer.
    """
    try:
        configured = int(os.environ.get("NEXTPASS_MAX_WORKERS", DEFAULT_MAX_WORKERS))
        if configured <= 0:
            configured = DEFAULT_MAX_WORKERS
    except ValueError:
        configured = DEFAULT_MAX_WORKERS
    return max(1, min(num_tasks, configured))


def resolve_platform(kml_path: Path, platform_by_name: dict[str, str]) -> str | None:
    """Resolve the platform tag for a KML file against a name->platform map.

    Tries, in order: (1) a direct stem match, (2) a match after dropping the
    leading token (e.g. mission prefix) from the stem, (3) a substring match
    against any known key. Returns None if nothing matches or the map is empty.
    """
    if not platform_by_name:
        return None
    stem = kml_path.stem.lower()
    platform = platform_by_name.get(stem)
    if platform is None and "_" in stem:
        stem_id = "_".join(stem.split("_")[1:])
        platform = platform_by_name.get(stem_id)
    if platform is None:
        for key, value in platform_by_name.items():
            if key in stem:
                platform = value
                break
    return platform


def sync_scratch_directory(
    urls: list[str],
    mission_name: str,
    scratch_dir: Path,
    logger: logging.Logger,
    step_cb: Callable[[str], None] | None = None,
) -> list[Path]:
    """
    Synchronize local scratch directory with online ESA URLs.

    Downloads missing files (concurrently, on a bounded thread pool) and
    removes obsolete files.

    Args:
        urls (List[str]): List of ESA download URLs.
        mission_name (str): Mission prefix (e.g., sentinel1, sentinel2).
        scratch_dir (Path): Local scratch directory.
        logger (logging.Logger): Logger for status updates.
        step_cb (Optional[Callable[[str], None]]): Optional callback invoked
            with a coarse progress label (e.g. "Downloading 2/5 files") as
            downloads complete.

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
        except OSError as e:
            logger.error("Failed to delete %s: %s", file_path, e)

    # Map each url to its target path, preserving order
    url_paths = [
        (url, scratch_dir / f"{mission_name}_{Path(url).stem}.kml") for url in urls
    ]

    # Determine which files are missing and need downloading
    to_download = [
        (url, file_path)
        for url, file_path in url_paths
        if file_path.name in missing_files or not file_path.exists()
    ]

    # Download missing files concurrently (network-bound)
    failed: set = set()
    if to_download:

        def _download(item):
            url, file_path = item
            try:
                download_kml(url, str(file_path))
                return None
            except (requests.RequestException, OSError) as e:
                logger.error("Failed to download %s: %s", url, e)
                return file_path

        total = len(to_download)
        completed = 0
        with ThreadPoolExecutor(max_workers=_resolve_max_workers(total)) as executor:
            futures = {executor.submit(_download, item): item for item in to_download}
            for future in as_completed(futures):
                url, file_path = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    # Safety net for the thread-pool retrieval boundary: _download
                    # already handles the expected requests/OSError failures above,
                    # so this only fires for a genuinely unexpected worker error and
                    # must not abort the rest of the batch.
                    logger.error("Failed to download %s: %s", url, e)
                    result = file_path
                if result is not None:
                    failed.add(result)
                completed += 1
                if step_cb:
                    step_cb(f"Downloading {completed}/{total} files")

        if failed:
            logger.error("%d of %d files failed to download.", len(failed), total)

    # Return local paths in original url order, skipping failed downloads
    local_kml_paths: list[Path] = [
        file_path for _, file_path in url_paths if file_path not in failed
    ]

    return local_kml_paths


def build_sentinel_collection(
    urls: list[str],
    n_day_past: float,
    mission_name: str,
    out_filename: str,
    logger: logging.Logger,
    platforms: list | None = None,
    step_cb: Callable[[str], None] | None = None,
) -> Path:
    """
    Download, parse, and merge Sentinel acquisition plans into a GeoJSON file.

    Downloads and parses for each KML file may execute concurrently on a
    bounded thread pool; results are order-independent and re-sorted by
    ``begin_date`` before being written out.

    Args:
        urls (List[str]): List of ESA download URLs.
        n_day_past (float): Number of days back to retain in the collection.
        mission_name (str): Name prefix for output filenames.
        out_filename (str): Final GeoJSON output filename.
        logger (logging.Logger): Logger object for status reporting.
        platforms (list | None): Optional per-URL platform tags, aligned by
            index with ``urls``. Used to build a lookup keyed by URL stem so
            each parsed KML can be tagged with its platform (see
            ``resolve_platform``).
        step_cb (Optional[Callable[[str], None]]): Optional callback invoked
            with a coarse progress label as downloads complete.

    Returns:
        Path: Path to the generated GeoJSON file.
    """
    out_path = SCRATCH_DIR / out_filename
    SCRATCH_DIR.mkdir(exist_ok=True)

    # Sync scratch directory with online files
    local_kml_paths = sync_scratch_directory(
        urls, mission_name, SCRATCH_DIR, logger, step_cb=step_cb
    )

    # Build platform mapping if platforms list is provided
    platform_by_name: dict[str, str] = {}
    if platforms:
        platform_by_name = {Path(u).stem.lower(): p for u, p in zip(urls, platforms)}

    def _load_kml(kml_path: Path):
        """Read cached geojson or parse KML (CPU-bound), tag with platform.

        Returns the tagged GeoDataFrame, or one of the module-level sentinels
        on failure: ``_PARSE_EMPTY`` for legitimately empty data (not counted
        as a failure) or ``_PARSE_FAILED`` when reading/parsing raised.
        """
        collection_path = SCRATCH_DIR / f"{kml_path.stem}.geojson"

        try:
            if collection_path.exists():
                logger.debug("Using cached file: %s", collection_path)
                gdf = gpd.read_file(collection_path)
            else:
                logger.debug("Parsing new file: %s", kml_path)
                gdf = parse_kml(kml_path)
                if gdf.empty:
                    logger.warning("No valid data in file: %s", kml_path)
                    return _PARSE_EMPTY
                gdf.to_file(collection_path)
        except (OSError, ValueError, AttributeError, SyntaxError) as e:
            logger.error("Failed to parse %s: %s", kml_path, e)
            return _PARSE_FAILED

        gdf["platform"] = resolve_platform(kml_path, platform_by_name)
        return gdf

    # Parse/read each KML concurrently. Order is irrelevant: the results are
    # concatenated and re-sorted by begin_date below. Each writes a distinct
    # geojson path, so there is no write collision.
    gdfs = []
    if local_kml_paths:
        total = len(local_kml_paths)
        failed_count = 0
        with ThreadPoolExecutor(max_workers=_resolve_max_workers(total)) as executor:
            futures = {
                executor.submit(_load_kml, kml_path): kml_path
                for kml_path in local_kml_paths
            }
            for future in as_completed(futures):
                kml_path = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    # Safety net for the thread-pool retrieval boundary: _load_kml
                    # already handles the expected read/parse failures above, so
                    # this only fires for a genuinely unexpected worker error and
                    # must not abort the rest of the batch.
                    logger.error("Failed to parse %s: %s", kml_path, e)
                    result = _PARSE_FAILED
                if result is _PARSE_FAILED:
                    failed_count += 1
                elif result is not _PARSE_EMPTY:
                    gdfs.append(result)

        if failed_count:
            logger.error("%d of %d files failed to parse.", failed_count, total)

    if not gdfs:
        logger.error("No valid GeoDataFrames created.")
        return Path()

    n_days_earlier = datetime.now(UTC) - timedelta(days=n_day_past)

    full_gdf = pd.concat(gdfs).drop_duplicates()
    full_gdf["begin_date"] = pd.to_datetime(full_gdf["begin_date"], utc=True)
    full_gdf["end_date"] = pd.to_datetime(full_gdf["end_date"], utc=True)
    full_gdf = full_gdf.loc[full_gdf["begin_date"] >= n_days_earlier]
    full_gdf = full_gdf.sort_values("begin_date").reset_index(drop=True)
    try:
        full_gdf.to_file(out_path)
        logger.info("%s collection saved to: %s", mission_name, out_path)
    except (OSError, ValueError) as e:
        logger.error("Failed to write final output file: %s", e)
        return Path()

    return out_path
