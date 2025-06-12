import colorsys
import re
import logging
import random
import folium
import matplotlib.pyplot as plt
from shapely.geometry import box, Polygon
import geopandas as gpd
from jinja2 import Template
from branca.element import MacroElement
from matplotlib.colors import to_hex
from utils import bbox_type, create_polygon_from_kml

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
LOGGER = logging.getLogger(__name__)


def hsl_distinct_colors(n):
    colors = []
    for i in range(n):
        # Generate colors with different hues
        hue = i / float(n)
        color = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
        # Convert from RGB (0-1) to hex (#RRGGBB)
        rgb = [int(c * 255) for c in color]
        hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
        colors.append(hex_color)
    return colors


def spread_rgb_colors(n):
    colors = []
    # Divide the color space into n parts
    step = 255 // n
    for i in range(n):
        # Spread out the color values across the RGB spectrum
        r = (i * step) % 256
        g = ((i + 1) * step) % 256
        b = ((i + 2) * step) % 256
        hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)
        colors.append(hex_color)
    return colors


def hsl_distinct_colors_improved(num_colors):
    colors = []
    for i in range(num_colors):
        hue = (i * 360 / num_colors) % 360
        saturation = random.randint(60, 80)
        lightness = random.randint(30, 50)
        r, g, b = colorsys.hls_to_rgb(
            hue / 360, lightness / 100, saturation / 100
            )
        hex_color = "#{:02x}{:02x}{:02x}".format(
            int(r * 255), int(g * 255), int(b * 255)
            )
        colors.append(hex_color)

    return colors


def make_opera_granule_map(results_dict, bbox, timestamp_dir):
    """
    Create an interactive map displaying OPERA granules for all datasets
    with download links.
    """
    output_file = timestamp_dir / "opera_products_map.html"

    # Parse AOI center for initial map centering
    bbox = bbox_type(bbox)
    if isinstance(bbox, str):
        geometry = create_polygon_from_kml(bbox)
        AOI = geometry.bounds
        AOI_polygon = geometry
    else:
        lat_min, lat_max, lon_min, lon_max = bbox
        AOI = (lon_min, lat_min, lon_max, lat_max)
        AOI_polygon = box(*AOI) 

    center_lat = (AOI[1] + AOI[3]) / 2
    center_lon = (AOI[0] + AOI[2]) / 2

    # Initialize base map
    map_object = folium.Map(location=[center_lat, center_lon], zoom_start=7)
    # Add AOI bounding box
    aoi_geojson = gpd.GeoSeries([AOI_polygon]).__geo_interface__
    folium.GeoJson(
        aoi_geojson,
        name="AOI",
        style_function=lambda x: {
            "color": "black",
            "weight": 2,
            "fillOpacity": 0.0
        }
    ).add_to(map_object)
    folium.TileLayer("Esri.WorldImagery").add_to(map_object)
    # Generate distinct colors for layers
    cmap = plt.get_cmap("tab20")
    dataset_names = list(results_dict.keys())
    colors = [to_hex(cmap(i % 20)) for i in range(len(dataset_names))]
    legend_entries = []

    for i, (dataset, data) in enumerate(results_dict.items()):
        gdf = data.get("gdf")
        if gdf is None or gdf.empty:
            LOGGER.info(f"Skipping {dataset}: empty or missing GeoDataFrame.")
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

            except Exception as e:
                LOGGER.info(f"Unexpected error: {e}")
                download_url = "URL not available"
                label = "OPERA Granule"

            gdf.iloc[idx, gdf.columns.get_loc("URL")] = download_url
            gdf.iloc[idx, gdf.columns.get_loc("GranuleUR")] = label
        # set the color of the icon and gemetry
        color = colors[i]
        style = {
            "color": color,
            "fillColor": color,
            "weight": 2,
            "fillOpacity": 0.5,
        }
        # Create a FeatureGroup to hold both geometries and markers
        feature_group = folium.FeatureGroup(name=dataset)
        # Add geometries to FeatureGroup
        folium.GeoJson(
            gdf.__geo_interface__,
            style_function=lambda x, style=style: style,
        ).add_to(feature_group)

        # Add popup markers to FeatureGroup
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
                    color="lightgray", icon_color=color, icon="cloud-download"
                ),
            ).add_to(feature_group)

        # Add the FeatureGroup to the map
        feature_group.add_to(map_object)
        legend_entries.append((dataset, color))
    
    # Add layer control
    folium.LayerControl().add_to(map_object)

    # Add legend
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

    # Save and retrun
    map_object.save(output_file)
    LOGGER.info(f"-> OPERA granules Map successfully saved to {output_file}")
    return map_object


def make_overpasses_map(result_s1, result_s2, result_l, bbox, timestamp_dir):
    """
    Create an interactive map displaying Sentinel
    and Landsat overpasses
    """
    output_file = timestamp_dir / "satellite_overpasses_map.html"

    satellite_results = {
        "Sentinel-1": result_s1,
        "Sentinel-2": result_s2,
        "landsat": result_l,
    }
    satellites = {}
    for name, result in satellite_results.items():
        if result:
            next_collect_info = result.get("next_collect_info", "No collection info available")
            next_collect_geometry = result.get("next_collect_geometry", None)
            satellites[name] = (next_collect_info, next_collect_geometry)

    # Parse AOI center for initial map centering
    bbox = bbox_type(bbox)
    if isinstance(bbox, str):
        geometry = create_polygon_from_kml(bbox)
        AOI = geometry.bounds
        AOI_polygon = geometry
    else:
        lat_min, lat_max, lon_min, lon_max = bbox
        AOI = (lon_min, lat_min, lon_max, lat_max)
        AOI_polygon = box(*AOI)

    center_lat = (AOI[1] + AOI[3]) / 2
    center_lon = (AOI[0] + AOI[2]) / 2
    # Base map
    map_object = folium.Map(location=[center_lat, center_lon], zoom_start=5)
    # Add AOI bounding box
    aoi_geojson = gpd.GeoSeries([AOI_polygon]).__geo_interface__
    folium.GeoJson(
        aoi_geojson,
        name="AOI",
        style_function=lambda x: {
            "color": "black",
            "weight": 2,
            "fillOpacity": 0.0
        }
    ).add_to(map_object)
    for sat_name, (info_text, geometry_list) in satellites.items():
        # Clean and split info
        lines = info_text.split("\n")
        cleaned_info = [line for line in lines if re.search(r'[1-9]', line)]
        info_list = cleaned_info
        num_polygons = len(geometry_list)

        # Generate distinct colors
        colors = hsl_distinct_colors_improved(num_polygons)

        # Handle Landsat 8/9 with odd/even row separation
        if "landsat" in sat_name:
            fg_even = folium.FeatureGroup(name=f"{sat_name} 8")
            fg_odd = folium.FeatureGroup(name=f"{sat_name} 9")

            for i, (polygon, info) in enumerate(zip(geometry_list, info_list)):
                group = fg_even if i % 2 == 0 else fg_odd
                color = "red" if i % 2 == 0 else "blue"

                geojson_data = gpd.GeoSeries([polygon]).__geo_interface__
                folium.GeoJson(
                    geojson_data,
                    name=f"{sat_name} Path/Row",
                    style_function=lambda x, color=color: {
                        "color": color,
                        "weight": 2,
                        "fillOpacity": 0.3
                    },
                    popup=folium.Popup(f"{sat_name}: {info}", max_width=300)
                ).add_to(group)

            fg_even.add_to(map_object)
            fg_odd.add_to(map_object)
        else:
            # Other satellites (Sentinel etc.)
            fg = folium.FeatureGroup(name=sat_name)
            for i, (polygon, info) in enumerate(
                    zip(geometry_list, info_list), start=1):
                if isinstance(polygon, Polygon):
                    color = colors[i - 1]
                    geojson_data = gpd.GeoSeries([polygon]).__geo_interface__
                    folium.GeoJson(
                        geojson_data,
                        name=f"{sat_name} Area {i}",
                        style_function=lambda x, color=color: {
                            "color": color,
                            "weight": 2,
                            "fillOpacity": 0.3
                        },
                        popup=folium.Popup(f"{sat_name}: {info}",
                                        max_width=300)
                    ).add_to(fg)
            fg.add_to(map_object)
    # Add LayerControl to toggle on/off
    folium.LayerControl(collapsed=False).add_to(map_object)
    # Save and retrun
    map_object.save(output_file)
    return map_object
