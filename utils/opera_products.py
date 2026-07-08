import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import earthaccess
import leafmap
import pandas as pd
from dateutil.relativedelta import relativedelta
from openpyxl import Workbook
from openpyxl.styles import Font

from utils.cloudiness import get_cloudiness
from utils.utils import bbox_to_geometry, bbox_type

LOGGER = logging.getLogger(__name__)


def find_print_available_opera_products(
    bbox,
    number_of_dates: int,
    date_str: str,
    list_of_products: list | None,
    timestamp_dir: Path,
) -> dict:
    """
    Query NASA/OPERA products over an AOI and return recent granules.

    Parameters
    ----------
    bbox :
        Either a KML path or a set of bbox coordinates, passed through bbox_type().
    number_of_dates : int
        Number of recent acquisition dates to keep.
    date_str : str
        "today" or YYYY-MM-DD.
    list_of_products : list | None
        Optional list of OPERA short names (without OPERA_L2/L3 prefixes).

    Returns
    -------
    dict
        Mapping dataset -> {"results": [...], "gdf": GeoDataFrame}
    """
    if list_of_products:
        prefix = "OPERA_L3_"
        prefix_special = "OPERA_L2_"

        # Apply conditional prefixing
        opera_datasets = [
            (
                (prefix_special + item)
                if ("RTC" in item or "CSLC" in item)
                else (prefix + item)
            )
            for item in list_of_products
        ]
    else:
        opera_datasets = [
            "OPERA_L3_DSWX-HLS_V1",
            "OPERA_L3_DSWX-S1_V1",
            "OPERA_L3_DIST-ALERT-HLS_V1",
            "OPERA_L3_DIST-ANN-HLS_V1",
            "OPERA_L2_RTC-S1_V1",
            "OPERA_L2_CSLC-S1_V1",
            "OPERA_L3_DISP-S1_V1",
        ]

    # Parse the bbox argument
    bbox_parsed = bbox_type(bbox)

    aoi_polygon, aoi, centroid = bbox_to_geometry(bbox_parsed, timestamp_dir)

    is_range = False

    # Check if the user provided a strict date range
    if "/" in date_str:
        try:
            start_str, end_str = date_str.split("/", 1)
            start_date = datetime.strptime(start_str, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_str, "%Y-%m-%d").date()
        except ValueError as e:
            msg = "Invalid --event-date range. Use YYYY-MM-DD/YYYY-MM-DD."
            raise ValueError(msg) from e

        if start_date > end_date:
            msg = "Invalid --event-date range: start date must be <= end date."
            raise ValueError(msg)

        start_date_recent = f"{start_date:%Y-%m-%d}T00:00:00"
        end_date_recent = f"{end_date:%Y-%m-%d}T23:59:59"
        is_range = True
    else:
        # Standard Single Date Logic
        if date_str == "today":
            today = datetime.now(timezone.utc).date()
        else:
            today = datetime.strptime(date_str, "%Y-%m-%d").date()

        one_year_ago = today - relativedelta(months=12)
        start_date_recent = f"{one_year_ago:%Y-%m-%d}T00:00:00"
        end_date_recent = f"{today:%Y-%m-%d}T23:59:59"

    results_dict: dict = {}
    LOGGER.info("** Available OPERA Products for Selected AOI **")
    for dataset in opera_datasets:
        LOGGER.info("* Searching %s ...", dataset)

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                results, gdf = leafmap.nasa_data_search(
                    short_name=dataset,
                    cloud_hosted=True,
                    bounding_box=aoi,
                    temporal=(start_date_recent, end_date_recent),
                    return_gdf=True,
                )

                if gdf is not None and not gdf.empty:
                    gdf = gdf.copy()
                    gdf["original_index"] = gdf.index
                    gdf["BeginningDateTime"] = pd.to_datetime(
                        gdf["BeginningDateTime"],
                    )

                    # If a strict range was requested, we keep everything the API returned
                    if is_range:
                        pass
                    # Otherwise, apply the standard 'number_of_dates' slice
                    else:
                        # Extract unique acquisition dates
                        gdf["AcqDate"] = gdf["BeginningDateTime"].dt.date
                        unique_dates = gdf.sort_values(
                            "BeginningDateTime", ascending=False
                        )["AcqDate"].unique()
                        selected_dates = unique_dates[:number_of_dates]

                        # Keep all granules that match selected dates
                        gdf = gdf[gdf["AcqDate"].isin(selected_dates)]
                        gdf = gdf.drop(columns=["AcqDate"])

                    # Final formatting
                    gdf["BeginningDateTime"] = gdf["BeginningDateTime"].dt.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                    )
                    results = [results[k] for k in gdf["original_index"]]
                    gdf = gdf.drop(columns=["original_index"])
                    LOGGER.info(
                        "-> Success: %s → %d granule(s) saved.", dataset, len(gdf)
                    )
                    results_dict[dataset] = {
                        "results": results,
                        "gdf": gdf,
                    }
                    break
                else:
                    LOGGER.info("xxx Attempt %d: No granules for %s.", attempt, dataset)
            except Exception as e:  # noqa: BLE001
                LOGGER.info(
                    "xxx Attempt %d: Error fetching %s: %s", attempt, dataset, e
                )

            if attempt < max_attempts:
                time.sleep(2**attempt)
            else:
                LOGGER.info(
                    "-> Failed to fetch %s after %d attempts.",
                    dataset,
                    max_attempts,
                )

    return results_dict


def fetch_hls_granule_links(granule_id: str) -> list:
    """Fetch the CMR metadata for a specific HLS granule ID to get download links."""
    collection = "HLSS30" if "S30" in granule_id else "HLSL30"
    try:
        results = earthaccess.search_data(
            short_name=collection,
            granule_ur=granule_id
        )
        if results:
            return results[0].data_links()
    except Exception as e:
        LOGGER.error("Failed to fetch HLS granule %s: %s", granule_id, e)
    
    return []


def describe_cloud_cover(cover_percent: float) -> str:
    """Return a short description string for a given cloud cover %."""
    if cover_percent > 75:
        description = "mostly cloudy"
    elif cover_percent > 50:
        description = "partly cloudy"
    else:
        description = "mostly clear"

    return (
        f"-> Based on OPERA HLS CLOUD layer, the scene is "
        f"{description}: {cover_percent:.2f}%"
    )


def export_opera_products(
        results_dict: dict,
        timestamp_dir,
        result_s1=None,
        compute_cloudiness: bool = True,
        include_hls: bool = False
) -> None:
    """
    Export OPERA products to an Excel file and log cloudiness summary.

    Parameters
    ----------
    results_dict : dict
        Output of find_print_available_opera_products().
    timestamp_dir :
        Output directory (Path-like) where the Excel will be written.
    result_s1 :
        Currently unused, kept for API compatibility.
    compute_cloudiness : bool
        Whether to compute cloudiness from CLOUD layers. Set to False to skip and save time.
    include_hls : bool
        Whether to include HLS products in the export. Set to False to skip HLS products in xls output.
    """
    output_file = timestamp_dir / "opera_products_metadata.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "OPERA Metadata"

    # Define bold font for header
    bold_font = Font(bold=True)
    headers = [
        "Dataset",
        "Granule ID",
        "Start Time",
        "End Time",
        "CLOUD PERC (%)",
        "Download URL WTR",
        "Download URL BWTR",
        "Download URL CONF",
        "Download URL VEG-ANOM-MAX",
        "Download URL VEG-DIST-STATUS",
        "Download URL VEG-DIST-DATE",
        "Download URL VEG-DIST-CONF",
        "Download URL RTC-VV",
        "Download URL RTC-VH",
        "Download URL CSLC-VV",
        "Geometry (WKT)",
    ]

    if include_hls:
        headers.extend([
            "Source HLS Granule ID", 
            "HLS Download URL (B04/Red)", 
            "HLS Download URL (B03/Green)", 
            "HLS Download URL (B02/Blue)",
            "HLS Download URL (B8A/B05/NIR)"
        ])
    ws.append(headers)

    # Apply bold to header cells
    for cell in ws[1]:
        cell.font = bold_font

    # Freeze header row (so row 1 stays visible when scrolling)
    ws.freeze_panes = "A2"

    cover_description: str | None = None

    for dataset, data in results_dict.items():
        results = data.get("results", [])
        gdf = data.get("gdf")

        if gdf is None or gdf.empty:
            LOGGER.warning(
                "Skipping geometry for dataset %s: No valid GeoDataFrame.",
                dataset,
            )
            geometries = [None] * len(results)
        else:
            geometries = list(gdf.geometry)

        overall_cloudy_area = 0.0
        overall_area = 0.0

        for idx, item in enumerate(results):
            umm = item.get("umm", {})
            granule_id = umm.get("GranuleUR", "N/A")
            temporal = umm.get("TemporalExtent", {})
            start_time = temporal.get("RangeDateTime", {}).get(
                "BeginningDateTime",
                "N/A",
            )
            end_time = temporal.get("RangeDateTime", {}).get(
                "EndingDateTime",
                "N/A",
            )

            urls = {
                "water": "N/A",
                "bwater": "N/A",
                "water_conf": "N/A",
                "veg_anom_max": "N/A",
                "veg_dist_status": "N/A",
                "veg_dist_date": "N/A",
                "veg_dist_conf": "N/A",
                "rtc-vv": "N/A",
                "rtc-vh": "N/A",
                "cslc-vv": "N/A",
                "cloud": "N/A",
            }

            keyword_map = {
                "B01_WTR": "water",
                "BWTR": "bwater",
                "B03_CONF": "water_conf",
                "VEG-ANOM-MAX": "veg_anom_max",
                "VEG-DIST-STATUS": "veg_dist_status",
                "VEG-DIST-DATE": "veg_dist_date",
                "VEG-DIST-CONF": "veg_dist_conf",
                "_30_v1.0_VV": "rtc-vv",
                "_30_v1.0_VH": "rtc-vh",
                "_VV_v1.1": "cslc-vv",
                "CLOUD": "cloud",
            }

            related_urls = umm.get("RelatedUrls", [])
            for url_entry in related_urls:
                url = url_entry.get("URL", "")
                if not url.startswith("https://"):
                    continue
                if not (url.endswith(".tif") or url.endswith(".h5")):
                    continue
                for keyword, key in keyword_map.items():
                    if keyword in url:
                        urls[key] = url

            # add geometry if available
            geom = geometries[idx] if idx < len(geometries) else None
            geom_wkt = geom.wkt if geom is not None else "N/A"

            cloud_layer_url = urls["cloud"]
            cloud_cover_percent: float | str = "N/A"
            area = 0.0

            if compute_cloudiness and cloud_layer_url and cloud_layer_url != "N/A":
                result = get_cloudiness(cloud_layer_url)
                if result is not None:
                    cloud_cover_percent, area = result
                    overall_cloudy_area += area * cloud_cover_percent / 100.0
                    overall_area += area

            # Write base data row
            row_data = [
                dataset,
                granule_id,
                start_time,
                end_time,
                cloud_cover_percent,
                urls["water"],
                urls["bwater"],
                urls["water_conf"],
                urls["veg_anom_max"],
                urls["veg_dist_status"],
                urls["veg_dist_date"],
                urls["veg_dist_conf"],
                urls["rtc-vv"],
                urls["rtc-vh"],
                urls["cslc-vv"],
                geom_wkt,
            ]

            # Only check for HLS granules if it is an HLS-derived OPERA product
            if include_hls:
                # Initialize variables here to guarantee they exist
                hls_granule_id = "N/A"
                hls_red = "N/A"
                hls_green = "N/A"
                hls_blue = "N/A"
                hls_nir = "N/A"
                
                if "HLS" in dataset:
                    hls_links = []
                    
                    # Try to extract directly from InputGranules (Standard for DSWx)
                    input_granules = umm.get("InputGranules", [])
                    raw_hls = next((g for g in input_granules if g.startswith("HLS.")), "N/A")
                    
                    if raw_hls != "N/A":
                        parts = raw_hls.split(".")
                        hls_granule_id = ".".join(parts[:6]) if len(parts) >= 6 else raw_hls
                        hls_links = fetch_hls_granule_links(hls_granule_id)
                        
                    # Fallback: Search CMR dynamically via Tile ID and Date (Required for DIST)
                    else:
                        try:
                            # Extract Tile ID (e.g., T11SLT) from the OPERA GranuleUR
                            parts = granule_id.split('_')
                            tile_id = next((p for p in parts if p.startswith('T') and len(p) == 6), None)
                            
                            if tile_id and start_time != "N/A":
                                date_only = start_time.split("T")[0]
                                search_bounds = geom.bounds if geom else None
                                
                                # Query CMR for all HLS granules on that day over the bounding box
                                hls_results = earthaccess.search_data(
                                    short_name=["HLSS30", "HLSL30"],
                                    temporal=(f"{date_only}T00:00:00", f"{date_only}T23:59:59"),
                                    bounding_box=search_bounds
                                )
                                
                                # Filter the results to find the one matching the exact Tile ID
                                for r in hls_results:
                                    g_name = r.get("umm", {}).get("GranuleUR", "")
                                    if f".{tile_id}." in g_name:
                                        hls_links = r.data_links()
                                        hls_granule_id = ".".join(g_name.split(".")[:6])
                                        break
                        except Exception as e:
                            LOGGER.warning(f"Failed to dynamically locate HLS granule for {granule_id}: {e}")
                            
                    # Map bands based on HLSS30 or HLSL30 naming conventions
                    for href in hls_links:
                        if href.endswith(".tif"):
                            if "B04" in href or "band04" in href.lower():
                                hls_red = href
                            elif "B03" in href or "band03" in href.lower():
                                hls_green = href
                            elif "B02" in href or "band02" in href.lower():
                                hls_blue = href
                            elif "S30" in hls_granule_id and ("B8A" in href or "band8a" in href.lower()):
                                hls_nir = href
                            elif "L30" in hls_granule_id and ("B05" in href or "band05" in href.lower()):
                                hls_nir = href

                # Appends all 5 elements to keep data rows and headers perfectly 1-to-1
                row_data.extend([hls_granule_id, hls_red, hls_green, hls_blue, hls_nir])

            ws.append(row_data)
            
        if compute_cloudiness and overall_area > 0:
            overall_cloud_cover_percent = 100.0 * (overall_cloudy_area / overall_area)
            cover_description = describe_cloud_cover(overall_cloud_cover_percent)

    # Auto-adjust column widths
    for column in ws.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        adjusted_width = min(max_length + 2, 100)  # cap width when needed
        ws.column_dimensions[column[0].column_letter].width = adjusted_width

    # Save workbook
    wb.save(output_file)

    LOGGER.info("-> OPERA products metadata successfully saved to %s", output_file)
    if cover_description:
        LOGGER.info("%s", cover_description)
