import colorsys
import json
import logging
import random
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

import folium
import geopandas as gpd
import matplotlib.pyplot as plt
from branca.element import MacroElement
from jinja2 import Template
from matplotlib.colors import to_hex
from shapely.geometry import Polygon


from utils.utils import (
    bbox_type,
    bbox_to_geometry,
    check_opera_overpass_intersection,
    style_function_factory,
)

LOGGER = logging.getLogger(__name__)


def hsl_distinct_colors(n: int) -> list[str]:
    """Generate n distinct colors using HSV → RGB, returned as hex strings."""
    colors: list[str] = []
    for i in range(n):
        # Generate colors with different hues
        hue = i / float(n)
        color = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        # Convert from RGB (0–1) to hex (#RRGGBB)
        rgb = [int(c * 255) for c in color]
        hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
        colors.append(hex_color)
    return colors


def spread_rgb_colors(n: int) -> list[str]:
    """Generate n RGB-based colors spread across the spectrum."""
    colors: list[str] = []
    step = 255 // max(n, 1)
    for i in range(n):
        r = (i * step) % 256
        g = ((i + 1) * step) % 256
        b = ((i + 2) * step) % 256
        hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)
        colors.append(hex_color)
    return colors


def hsl_distinct_colors_improved(num_colors: int) -> list[str]:
    """Generate visually distinct HSL-based colors with random saturation/lightness."""
    colors: list[str] = []
    for i in range(num_colors):
        hue = (i * 360 / num_colors) % 360
        saturation = random.randint(60, 80)
        lightness = random.randint(30, 50)
        r, g, b = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
        hex_color = "#{:02x}{:02x}{:02x}".format(
            int(r * 255),
            int(g * 255),
            int(b * 255),
        )
        colors.append(hex_color)
    return colors


def make_opera_granule_map(
    results_dict: Dict[str, Dict[str, Any]],
    bbox: Any,
    timestamp_dir: Path,
):
    """
    Create an interactive map displaying OPERA granules for all datasets
    with download links.
    """
    output_file = timestamp_dir / "opera_products_map.html"

    # Parse AOI center for initial map centering
    bbox_parsed = bbox_type(bbox)
    aoi_polygon, aoi, centroid = bbox_to_geometry(bbox_parsed, timestamp_dir)
    center_lat = centroid.y
    center_lon = centroid.x

    # Initialize base map
    map_object = folium.Map(location=[center_lat, center_lon], zoom_start=7)

    # AOI bounding box
    aoi_geojson = gpd.GeoSeries([aoi_polygon]).__geo_interface__

    folium.TileLayer("Esri.WorldImagery").add_to(map_object)

    # Generate distinct colors for layers
    cmap = plt.get_cmap("tab20")
    dataset_names = list(results_dict.keys())
    colors = [to_hex(cmap(i % 20)) for i in range(len(dataset_names))]
    legend_entries: list[tuple[str, str]] = []

    for i, (dataset, data) in enumerate(results_dict.items()):
        gdf = data.get("gdf")
        if gdf is None or gdf.empty:
            LOGGER.info("Skipping %s: empty or missing GeoDataFrame.", dataset)
            continue

        gdf = gdf.copy()

        if i < 4:
            pos_delta = 0.08 * (i - 1)
        else:
            pos_delta = 0.08 * (i - 5)

        # Add download URL and name for popup
        for idx, item in enumerate(data["results"]):
            try:
                umm = item["umm"]
                download_url = "N/A"
                for url_entry in umm.get("RelatedUrls", []):
                    if url_entry.get("Type") == "GET DATA":
                        download_url = url_entry.get("URL", "N/A")
                        break
                label = umm.get("GranuleUR", "OPERA Granule")
            except Exception as e:  # noqa: BLE001
                LOGGER.info("Unexpected error while parsing UMM: %s", e)
                download_url = "URL not available"
                label = "OPERA Granule"

            gdf.iloc[idx, gdf.columns.get_loc("URL")] = download_url
            gdf.iloc[idx, gdf.columns.get_loc("GranuleUR")] = label

        # Set the color of the icon and geometry
        color = colors[i]
        style = {
            "color": color,
            "fillColor": color,
            "weight": 2,
            "fillOpacity": 0.5,
        }

        feature_group = folium.FeatureGroup(name=dataset)

        # Add geometries
        folium.GeoJson(
            gdf.__geo_interface__,
            style_function=lambda x, style=style: style,
        ).add_to(feature_group)

        # Add popup markers
        for _, row in gdf.iterrows():
            centroid = row.geometry.centroid
            popup_html = f"""
                <b>{row['GranuleUR']}</b><br>
                <a href="{row['URL']}" target="_blank">
                    Download Granule
                </a>
            """
            folium.Marker(
                location=[centroid.y + pos_delta, centroid.x + pos_delta],
                popup=folium.Popup(popup_html, max_width=400),
                icon=folium.Icon(
                    color="lightgray",
                    icon_color=color,
                    icon="cloud-download",
                ),
            ).add_to(feature_group)

        feature_group.add_to(map_object)
        legend_entries.append((dataset, color))

    # AOI outline
    folium.GeoJson(
        aoi_geojson,
        name="AOI",
        style_function=lambda x: {"color": "black", "weight": 2, "fillOpacity": 0.0},
    ).add_to(map_object)

    folium.LayerControl().add_to(map_object)

    # Legend template
    legend_html = """
    {% macro html(this, kwargs) %}
    <div style="position: fixed;
                bottom: 50px; left: 50px; width: 220px; height: auto;
                z-index:9999; font-size:14px;
                background-color: white;
                padding: 10px;
                border: 2px solid grey;
                border-radius: 5px;">
    <b>OPERA Products</b><br>
    {% for name, color in this.legend_items %}
        <div style="margin-bottom:4px">
            <span style="display:inline-block; width:12px; height:12px;
                        background-color:{{ color }}; margin-right:6px">
            </span>
            {{ name }}
        </div>
    {% endfor %}
    </div>
    {% endmacro %}
    """

    class Legend(MacroElement):
        def __init__(self, legend_items):
            super().__init__()
            self._template = Template(legend_html)
            self.legend_items = legend_items

    map_object.get_root().add_child(Legend(legend_entries))

    map_object.save(output_file)
    LOGGER.info("-> OPERA granules Map successfully saved to %s", output_file)
    return map_object


def make_opera_granule_drcs_map(
    event_date: datetime,
    results_dict: Dict[str, Dict[str, Any]],
    result_s1: dict | None,
    result_s2: dict | None,
    result_l: dict | None,
    bbox: Any,
    timestamp_dir: Path,
):
    """
    Create an interactive map displaying OPERA granules with DRCS logic.

    Shows available granules in color, and for pre-event granules
    shows overpass info from S1/S2/Landsat.
    """
    output_file = timestamp_dir / "opera_products_drcs_map.html"

    # Parse AOI
    bbox_parsed = bbox_type(bbox)
    aoi_polygon, aoi, centroid = bbox_to_geometry(bbox_parsed, timestamp_dir)
    center_lat = centroid.y
    center_lon = centroid.x

    map_object = folium.Map(location=[center_lat, center_lon], zoom_start=7)

    aoi_geojson = gpd.GeoSeries([aoi_polygon]).__geo_interface__
    folium.TileLayer("Esri.WorldImagery").add_to(map_object)

    cmap = plt.get_cmap("tab20")
    dataset_names = list(results_dict.keys())
    colors = [to_hex(cmap(i % 20)) for i in range(len(dataset_names))]
    legend_entries: list[tuple[str, str]] = []

    # Loop over OPERA products
    for i, (dataset, data) in enumerate(results_dict.items()):
        # Skip ANN at the moment
        if dataset == "OPERA_L3_DIST-ANN-HLS_V1":
            continue

        gdf = data.get("gdf")
        if gdf is None or gdf.empty:
            LOGGER.info("Skipping %s: empty or missing GeoDataFrame.", dataset)
            continue

        gdf = gdf.copy()
        if i < 4:
            pos_delta = 0.08 * (i - 1)
        else:
            pos_delta = 0.08 * (i - 5)

        feature_group = folium.FeatureGroup(name=dataset)
        gdf = gdf.reset_index(drop=True)

        # Add download URL and name, decorate popups with DRCS logic
        for idx, item in enumerate(data["results"]):
            try:
                umm = item["umm"]
                download_url = "N/A"
                for url_entry in umm.get("RelatedUrls", []):
                    if url_entry.get("Type") == "GET DATA":
                        download_url = url_entry.get("URL", "N/A")
                        break
                label = umm.get("GranuleUR", "OPERA Granule")

                parts = label.split("_")
                if len(parts) > 2 and parts[2] == "DISP-S1":
                    aqu_date = datetime.strptime(parts[7], "%Y%m%dT%H%M%SZ")
                else:
                    aqu_date = datetime.strptime(parts[4], "%Y%m%dT%H%M%SZ")
                aqu_date_utc = aqu_date.replace(tzinfo=timezone.utc)
            except Exception as e:
                LOGGER.info("Unexpected error while parsing UMM/label: %s", e)
                download_url = "URL not available"
                label = "OPERA Granule"
                aqu_date_utc = event_date - timezone.utc.utcoffset(
                    event_date
                )  # force pre-event

            geom = gdf.iloc[idx].geometry
            centroid = geom.centroid

            condition_ok = aqu_date_utc > event_date

            if condition_ok:
                color = colors[i]
                popup_html = f"""
                    <b>{label}</b><br>
                    <a href="{download_url}" target="_blank">
                        Download Granule
                    </a>
                """
                url_value = download_url
                label_value = label
            else:
                if aoi_polygon.area > 0:
                    product_geom = geom.intersection(aoi_polygon)
                else:
                    product_geom = geom
                report = check_opera_overpass_intersection(
                    label,
                    product_geom,
                    result_s1,
                    result_s2,
                    result_l,
                    event_date,
                    dataset_name=dataset,
                )
                color = "lightgray"
                sentences_html = (
                    report.replace("\n", "<br>")
                    if report
                    else "No overpass info available."
                )
                popup_html = f"""
                <b>Not available yet:</b><br>
                {sentences_html}
                """
                url_value = "N/A"
                label_value = f"{label} (old granule)"

            # Update GeoDataFrame
            gdf.at[idx, "URL"] = url_value
            gdf.at[idx, "GranuleUR"] = label_value
            gdf.at[idx, "condition_ok"] = condition_ok

            folium.Marker(
                location=[centroid.y + pos_delta, centroid.x + pos_delta],
                popup=folium.Popup(popup_html, max_width=800),
                icon=folium.Icon(
                    color=color if condition_ok else "lightgray",
                    icon_color="white",
                    icon="cloud-download" if condition_ok else "info-sign",
                ),
            ).add_to(feature_group)

        style_func = style_function_factory(colors[i])
        folium.GeoJson(
            data=json.loads(gdf.to_json()),
            style_function=style_func,
            name=f"{dataset}_geojson",
        ).add_to(feature_group)

        feature_group.add_to(map_object)
        legend_entries.append((dataset, colors[i]))

    # AOI layer
    folium.GeoJson(
        aoi_geojson,
        name="AOI",
        style_function=lambda x: {"color": "black",
                                  "weight": 2, "fillOpacity": 0.0},
    ).add_to(map_object)

    folium.LayerControl().add_to(map_object)

    legend_html = """
    {% macro html(this, kwargs) %}
    <div style="position: fixed;
                bottom: 50px; left: 50px; width: 220px; height: auto;
                z-index:9999; font-size:14px;
                background-color: white;
                padding: 10px;
                border: 2px solid grey;
                border-radius: 5px;">
    <b>OPERA Products</b><br>
    {% for name, color in this.legend_items %}
        <div style="margin-bottom:4px">
            <span style="display:inline-block; width:12px; height:12px;
                        background-color:{{ color }}; margin-right:6px">
            </span>
            {{ name }}
        </div>
    {% endfor %}
    </div>
    {% endmacro %}
    """

    class Legend(MacroElement):
        def __init__(self, legend_items):
            super().__init__()
            self._template = Template(legend_html)
            self.legend_items = legend_items

    map_object.get_root().add_child(Legend(legend_entries))

    map_object.save(output_file)
    LOGGER.info(
        "-> DRCS OPERA granules Map successfully saved to %s",
        output_file,
    )
    return map_object


def make_overpasses_map(
    result_s1: dict | None,
    result_s2: dict | None,
    result_l: dict | None,
    result_nisar: dict | None,
    bbox: Any,
    timestamp_dir: Path,
):
    """
    Create an interactive map displaying Sentinel and Landsat overpasses.
    """
    output_file = timestamp_dir / "satellite_overpasses_map.html"

    satellite_results = {
        "Sentinel-1": result_s1,
        "Sentinel-2": result_s2,
        "landsat": result_l,
        "NISAR": result_nisar,
    }

    satellites: dict[str, tuple[str, list | None]] = {}
    for name, result in satellite_results.items():
        if result:
            next_collect_info = result.get(
                "next_collect_info",
                "No collection info available",
            )
            next_collect_geometry = result.get("next_collect_geometry", None)
            satellites[name] = (next_collect_info, next_collect_geometry)

    # Parse AOI
    bbox_parsed = bbox_type(bbox)
    aoi_polygon, aoi, centroid = bbox_to_geometry(bbox_parsed, timestamp_dir)
    center_lat = centroid.y
    center_lon = centroid.x

    map_object = folium.Map(location=[center_lat, center_lon], zoom_start=5)
    aoi_geojson = gpd.GeoSeries([aoi_polygon]).__geo_interface__

    for sat_name, (info_text, geometry_list) in satellites.items():
        if not geometry_list:
            continue

        info_list = satellite_results[sat_name].get("next_collect_summary")
        if not info_list:
            lines = info_text.split("\n")
            cleaned_info = [line for line in lines if re.search(r"[1-9]", line)]
            info_list = cleaned_info
        num_polygons = len(geometry_list)

        colors = hsl_distinct_colors_improved(num_polygons)

        if "landsat" in sat_name:
            fg_8_asc = folium.FeatureGroup(name=f"{sat_name} 8 Ascending")
            fg_8_desc = folium.FeatureGroup(name=f"{sat_name} 8 Descending")
            fg_9_asc = folium.FeatureGroup(name=f"{sat_name} 9 Ascending")
            fg_9_desc = folium.FeatureGroup(name=f"{sat_name} 9 Descending")

            for i, (polygon, info) in enumerate(zip(geometry_list, info_list)):
                sat_num = 8 if i % 2 == 0 else 9

                if re.search(r"ascending", info, re.IGNORECASE):
                    asc_desc = "Ascending"
                elif re.search(r"descending", info, re.IGNORECASE):
                    asc_desc = "Descending"
                else:
                    asc_desc = "Unknown"

                if sat_num == 8 and asc_desc == "Ascending":
                    group = fg_8_asc
                    color = "red"
                elif sat_num == 8 and asc_desc == "Descending":
                    group = fg_8_desc
                    color = "darkred"
                elif sat_num == 9 and asc_desc == "Ascending":
                    group = fg_9_asc
                    color = "blue"
                elif sat_num == 9 and asc_desc == "Descending":
                    group = fg_9_desc
                    color = "darkblue"
                else:
                    group = fg_8_asc
                    color = "gray"

                geojson_data = gpd.GeoSeries([polygon]).__geo_interface__
                info_html = info.replace("\n", "<br>") if isinstance(info, str) else str(info)
                folium.GeoJson(
                    geojson_data,
                    name=f"{sat_name} Path/Row",
                    style_function=lambda x, color=color: {
                        "color": color,
                        "weight": 2,
                        "fillOpacity": 0.3,
                    },
                    popup=folium.Popup(f"<b>{sat_name}</b><br>{info_html}", max_width=600),
                ).add_to(group)

            fg_8_asc.add_to(map_object)
            fg_8_desc.add_to(map_object)
            fg_9_asc.add_to(map_object)
            fg_9_desc.add_to(map_object)
        else:
            fg = folium.FeatureGroup(name=sat_name)
            for i, (polygon, info) in enumerate(zip(geometry_list, info_list), start=1):
                if isinstance(polygon, Polygon):
                    color = colors[i - 1]
                    geojson_data = gpd.GeoSeries([polygon]).__geo_interface__
                    info_html = info.replace("\n", "<br>") if isinstance(info, str) else str(info)
                    folium.GeoJson(
                        geojson_data,
                        name=f"{sat_name} Area {i}",
                        style_function=lambda x, color=color: {
                            "color": color,
                            "weight": 2,
                            "fillOpacity": 0.3,
                        },
                        popup=folium.Popup(f"<b>{sat_name}</b><br>{info_html}", max_width=600),
                    ).add_to(fg)
            fg.add_to(map_object)

    folium.GeoJson(
        aoi_geojson,
        name="AOI",
        style_function=lambda x: {"color": "black", "weight": 2, "fillOpacity": 0.0},
    ).add_to(map_object)

    # NOAA tide stations — collect from all satellite results, deduplicate by ID
    noaa_stations: dict = {}
    for result in (result_s1, result_s2, result_l, result_nisar):
        if result and result.get("noaa_stations"):
            for st in result["noaa_stations"]:
                noaa_stations[st["id"]] = st

    if noaa_stations:
        fg_tide = folium.FeatureGroup(name="NOAA Tide Stations")
        for st in noaa_stations.values():
            popup_html = (
                f"<b>NOAA Station</b><br>"
                f"ID: {st['id']}<br>"
                f"Name: {st['name']}<br>"
                f"Lat: {st['lat']:.4f}, Lon: {st['lng']:.4f}"
            )
            folium.Marker(
                location=[st["lat"], st["lng"]],
                popup=folium.Popup(popup_html, max_width=250),
                icon=folium.Icon(color="blue", icon="tint", prefix="fa"),
            ).add_to(fg_tide)
        fg_tide.add_to(map_object)

    folium.LayerControl(collapsed=False).add_to(map_object)

    class MultiPopup(MacroElement):
        """One popup open per type (overpass / station), independent of each other."""
        def __init__(self):
            super().__init__()
            self._template = Template("""
                {% macro script(this, kwargs) %}
                    L.Popup.prototype.options.autoClose = false;
                    L.Popup.prototype.options.closeOnClick = false;
                    var _lastStationPopup = null;
                    var _lastOverpassPopup = null;
                    var _map = {{ this._parent.get_name() }};

                    _map.on('popupopen', function(e) {
                        var source = e.layer || e.sourceTarget;
                        var isMarker = source && source instanceof L.Marker;
                        if (isMarker) {
                            if (_lastStationPopup && _lastStationPopup !== e.popup) {
                                _map.removeLayer(_lastStationPopup);
                            }
                            _lastStationPopup = e.popup;
                        } else {
                            if (_lastOverpassPopup && _lastOverpassPopup !== e.popup) {
                                _map.removeLayer(_lastOverpassPopup);
                            }
                            _lastOverpassPopup = e.popup;
                        }
                    });
                {% endmacro %}
            """)

    map_object.get_root().add_child(MultiPopup())

    map_object.save(output_file)
    LOGGER.info("-> Satellite overpasses map successfully saved to %s", output_file)
    return map_object
