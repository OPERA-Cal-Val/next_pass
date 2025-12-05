import logging
import time
from datetime import datetime, timezone

import leafmap
import pandas as pd
from dateutil.relativedelta import relativedelta
from openpyxl import Workbook
from openpyxl.styles import Font

from utils.cloudiness import get_cloudiness
from utils.utils import bbox_type, create_polygon_from_kml

LOGGER = logging.getLogger(__name__)


def find_print_available_opera_products(
    bbox,
    number_of_dates: int,
    date_str: str,
    list_of_products: list | None,
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

    if isinstance(bbox_parsed, str):
        # Create geometry from KML file
        geometry = create_polygon_from_kml(bbox_parsed)
        aoi = geometry.bounds
    else:
        # Extract bounding box
        lat_min, lat_max, lon_min, lon_max = bbox_parsed
        # (minx, miny, maxx, maxy)
        aoi = (lon_min, lat_min, lon_max, lat_max)

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

                    # Extract unique acquisition dates
                    gdf["AcqDate"] = gdf["BeginningDateTime"].dt.date
                    unique_dates = gdf.sort_values(
                        "BeginningDateTime", ascending=False
                    )["AcqDate"].unique()
                    selected_dates = unique_dates[:number_of_dates]

                    # Keep all granules that match selected dates
                    gdf = gdf[gdf["AcqDate"].isin(selected_dates)]

                    # Final formatting
                    gdf["BeginningDateTime"] = gdf["BeginningDateTime"].dt.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                    )
                    results = [results[k] for k in gdf["original_index"]]
                    gdf = gdf.drop(columns=["original_index", "AcqDate"])
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


def export_opera_products(results_dict: dict, timestamp_dir, result_s1=None) -> None:
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

            if cloud_layer_url and cloud_layer_url != "N/A":
                result = get_cloudiness(cloud_layer_url)
                if result is not None:
                    cloud_cover_percent, area = result
                    overall_cloudy_area += area * cloud_cover_percent / 100.0
                    overall_area += area

            # Write data row
            ws.append(
                [
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
            )

        if overall_area > 0:
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
