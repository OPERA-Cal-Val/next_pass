#!/usr/bin/env python3

import argparse
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, List
from utils.utils import format_satellite_arg

LOGGER = logging.getLogger("next_pass")

EXAMPLE = """
EXAMPLE USAGE:
Point (lat/lon pair):
  next-pass -b 34.20 -118.17

Bounding Box (SNWE):
  next-pass -b 34.15 34.25 -118.20 -118.15

KML File:
  next-pass -b /path/to/file.kml
"""


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser for CLI inputs."""
    from utils.utils import valid_drcs_datetime

    desc = "Find next satellite overpass date."
    parser = argparse.ArgumentParser(
        description=desc,
        epilog=EXAMPLE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-b",
        "--bbox",
        required=True,
        nargs="+",
        type=str,
        help=(
            "Bounding box: Either 2 or 4 floats (point or bbox) "
            "or a WKT-format string (POLYGON or POINT) "
            "or a link to a .geojson file (online)"
            "or a path to a .kml or .geojson location file"
        ),
    )
    parser.add_argument(
        "-s",
        "--sat",
        nargs="+",
        default="all",
        choices=["sentinel-1", "sentinel-2", "landsat", "nisar", "all"],
        help="Satellite mission. Default is all.",
    )
    parser.add_argument(
        "-k",
        "--look-back",
        type=int,
        default=13,
        help="Number of days to look back for past overpasses",
    )
    parser.add_argument(
        "-f",
        "--functionality",
        default="both",
        type=str,
        help="Functionality to run: overpasses, opera_search or both",
    )
    parser.add_argument(
        "-n",
        "--number-of-dates",
        default=5,
        type=int,
        help="Number of most recent dates to consider for OPERA products",
    )
    parser.add_argument(
        "-d",
        "--event-date",
        default="today",
        type=str,
        help="Date (UTC) in format YYYY-MM-DD OR a date range YYYY-MM-DD/YYYY-MM-DD to consider for OPERA products",
    )
    parser.add_argument(
        "-p",
        "--products",
        default=[],
        nargs="*",
        help="A list containing a subset of OPERA products to be searched",
    )
    parser.add_argument(
        "-c",
        "--cloudiness",
        action="store_true",
        help=(
            "Display cloudiness prediction and/or history for future and "
            "past overpasses, respectively"
        ),
    )
    parser.add_argument(
        "-t",
        "--tide",
        action="store_true",
        help=(
            "Display NOAA Tide Predictions for future and/or past overpasses. "
            "Note: For Landsat and NISAR, displayed passes are limited to those "
            "within 60 days (NOAA API forecast limit) to improve output readability; "
            "a summary note lists any passes scheduled beyond that window."
        ),
    )
    parser.add_argument(
        "-l",
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Set logging level (default: info).",
    )
    parser.add_argument(
        "--email",
        action="store_true",
        help="Send an email with the results.",
    )
    parser.add_argument(
        "-g",
        "--generate-drcs-html",
        type=valid_drcs_datetime,
        metavar="YYYY-MM-DDTHH:MM",
        help=(
            "Generate DRCS HTML file using a UTC event date "
            "in format YYYY-MM-DDTHH:MM"
        ),
    )
    return parser


def find_next_overpass(args: argparse.Namespace, timestamp_dir: Path) -> dict:
    """Main logic for finding the next satellite overpasses."""

    from utils.landsat_pass import next_landsat_pass
    from utils.nisar_pass import next_nisar_pass
    from utils.sentinel_pass import next_sentinel_pass
    from utils.utils import bbox_type, bbox_to_geometry
    from utils.cloudiness import api_limit_reached

    bbox = bbox_type(args.bbox)
    n_day_past = args.look_back

    pred_cloudiness = bool(args.cloudiness)
    pred_tide = bool(getattr(args, "tide", False))

    geometry, aoi, centroid = bbox_to_geometry(bbox, timestamp_dir)
    lat_min = centroid.y
    lon_min = centroid.x

    selected = args.sat

    if "all" in selected:
        selected = ["sentinel-1", "sentinel-2", "landsat", "nisar"]

    # Initialize outputs
    sentinel1 = []
    sentinel2 = []
    landsat = []
    nisar = []

    # for cloudiness waiting time
    needs_weather_backoff = (
            pred_cloudiness
            and "sentinel-1" in selected
            and "sentinel-2" in selected
        )

    # Fetch conditionally
    if "sentinel-1" in selected:
        LOGGER.info("Fetching Sentinel-1 data...")
        sentinel1 = next_sentinel_pass(
            "sentinel1", geometry, n_day_past, pred_cloudiness, pred_tide
        )

    if "sentinel-2" in selected:
        LOGGER.info("Fetching Sentinel-2 data...")

        if needs_weather_backoff and not api_limit_reached():
            LOGGER.info(
                "Waiting 1 min to avoid hitting cumulative weather API quota."
            )
            time.sleep(60)

        sentinel2 = next_sentinel_pass(
            "sentinel2", geometry, n_day_past, pred_cloudiness, pred_tide
        )

    if "nisar" in selected:
        LOGGER.info("Fetching NISAR data...")
        nisar = next_nisar_pass(geometry, n_day_past, arg_tide=pred_tide)

    if "landsat" in selected:
        LOGGER.info("Fetching Landsat data...")
        landsat = next_landsat_pass(lat_min, lon_min, geometry, n_day_past, pred_tide)

    return {
        "sentinel-1": sentinel1,
        "sentinel-2": sentinel2,
        "landsat": landsat,
        "nisar": nisar,
    }


def format_arg(bbox_arg: Any) -> str:
    """Pretty-print bbox argument for logs/emails."""
    if isinstance(bbox_arg, str):
        return bbox_arg
    if (
        isinstance(bbox_arg, list)
        and all(isinstance(x, str) for x in bbox_arg)
        and len(bbox_arg) in (1, 2, 4)
    ):
        return " ".join(bbox_arg)
    msg = "Argument must be a list of 1, 2, or 4 strings, or a single WKT/URL string."
    raise ValueError(msg)


def send_email(subject: str, body: str, attachment: Path | None = None) -> None:
    """
    Send an email with the next_pass information.

    Parameters
    ----------
    subject : str
        Subject of the email.
    body : str
        Body of the email.
    attachment : Path or None
        Optional attachment file path.
    """
    import yagmail

    gmail_user = "aria.hazards.jpl@gmail.com"
    gmail_pswd = os.environ["GMAIL_APP_PSWD"]
    yag = yagmail.SMTP(gmail_user, gmail_pswd)

    receivers = [
        "cole.speed@jpl.nasa.gov",
        "ines.fenni@jpl.nasa.gov",
        "emre.havazli@jpl.nasa.gov",
    ]
    yag.send(bcc=receivers, subject=subject, contents=[body], attachments=[attachment])


def run_next_pass(
    bbox: List[float] | str,
    number_of_dates: int = 5,
    date: str | None = None,
    functionality: str = "both",
    compute_cloudiness: bool = False,
    compute_tide: bool = False,
    products: List[str] | str | None = None,
    satellites: List[str] | str | None = None
):
    """
    Programmatic entry point for next_pass.
    Wraps main() and builds CLI-style args.
    """

    # Ensure bbox is packaged such that argparse receives 1 token (string/URL) or 4 tokens (SNWE)
    if isinstance(bbox, str):
        bbox_list = [bbox]
    else:
        bbox_list = list(map(str, bbox))

    if not compute_cloudiness:
        print("[INFO] Skipping HLS cloud cover calculation and prediction.")
    if not compute_tide:
        print("[INFO] Skipping NOAA tide prediction.")

    cli_args = [
        "-b",
        *bbox_list,
        "-n",
        str(number_of_dates),
        "-f",
        functionality,
    ]

    if compute_cloudiness:
        cli_args.append("-c")
    if compute_tide:
        cli_args.append("-t")

    if date:
        cli_args += ["-d", date]
    
    if products:
        cli_args.append("-p")
        if isinstance(products, str):
            cli_args.extend([products])
        else:
            cli_args.extend(products)

    if satellites:
        cli_args.append("-s")
        if isinstance(satellites, str):
            cli_args.extend([satellites])
        else:
            cli_args.extend(satellites)

    return main(cli_args)


def main(cli_args: Any = None):
    """Main entry point for the CLI or programmatic use."""
    if isinstance(cli_args, argparse.Namespace):
        args = cli_args
    elif cli_args is None:
        args = create_parser().parse_args()
    else:
        args = create_parser().parse_args(cli_args)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    from utils.opera_products import (
        export_opera_products,
        find_print_available_opera_products,
    )
    from utils.plot_maps import (
        make_opera_granule_drcs_map,
        make_opera_granule_map,
        make_overpasses_map,
    )
    from utils.utils import Tee

    # Create a timestamp string
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Create the output directory
    timestamp_dir = Path(f"nextpass_outputs_{timestamp}")
    timestamp_dir.mkdir(parents=True, exist_ok=True)

    log_file = timestamp_dir / "run_output.txt"
    log = open(log_file, "w", encoding="utf-8")

    # Mirror stdout/stderr to both terminal and log file
    sys.stdout = sys.stderr = Tee(sys.__stdout__, log)

    print(f"Log file created: {log_file}")
    print(f"BBox = {format_arg(args.bbox)}\n")

    result_s1 = result_s2 = result_l = result_nisar = None
    results_opera = None

    # Overpasses functionality
    if args.functionality in ("both", "overpasses"):
        result = find_next_overpass(args, timestamp_dir)
        result_s1 = result["sentinel-1"]
        result_s2 = result["sentinel-2"]
        result_l = result["landsat"]
        result_nisar = result["nisar"]

        make_overpasses_map(
            result_s1,
            result_s2,
            result_l,
            result_nisar,
            args.bbox,
            timestamp_dir,
        )

        # Print only missions that were requested / have results
        for mission, mission_result in result.items():
            if mission_result:
                print(f"\n=== {mission.upper()} ===")
                print(
                    mission_result.get(
                        "next_collect_info",
                        "No collection info available.",
                    )
                )

    # OPERA search functionality
    if args.functionality in ("both", "opera_search"):
        results_opera = find_print_available_opera_products(
            args.bbox,
            args.number_of_dates,
            args.event_date,
            args.products,
            timestamp_dir
        )
        export_opera_products(
            results_opera,
            timestamp_dir,
            compute_cloudiness=args.cloudiness
        )
        make_opera_granule_map(results_opera, args.bbox, timestamp_dir)

    # DRCS HTML map (requires both overpasses + OPERA)
    if args.generate_drcs_html is not None and args.functionality in ("both",):
        make_opera_granule_drcs_map(
            args.generate_drcs_html,
            results_opera,
            result_s1,
            result_s2,
            result_l,
            args.bbox,
            timestamp_dir,
        )

    # Optional email
    if args.email:
        overpasses_map = timestamp_dir / "satellite_overpasses_map.html"

        # Close log so everything is flushed, then read back
        log.close()
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__
        with open(log_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Skip the first few lines (log header, bbox line, etc.)
        email_body = "".join(lines[4:]) if len(lines) > 4 else "".join(lines)

        subject = (
            f"Next Satellite Overpasses for {format_satellite_arg(args.sat)} "
            f"as of {timestamp} UTC for AOI: {format_arg(args.bbox)}"
        )
        send_email(subject, email_body, overpasses_map)

        print("=========================================")
        print("Alert emailed to recipients.")
        print("=========================================")

    # Restore standard output and error
    sys.stdout = sys.__stdout__
    sys.stderr = sys.__stderr__

    # Ensure the log file is closed
    if not log.closed:
        log.close()

    return timestamp_dir


if __name__ == "__main__":
    main()
