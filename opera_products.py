import logging
import time
from datetime import date
from dateutil.relativedelta import relativedelta  
from plot_maps import hsl_distinct_colors_improved
from utils import create_polygon_from_kml, bbox_type  

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

# Base URLs
EARTH_DATA_URL = (
    "https://github.com/opengeos/NASA-Earth-Data/raw/main/nasa_earth_data.tsv"
)


def find_print_available_opera_products(args) -> dict:
    import leafmap  
    import csv

    opera_datasets = [
        'OPERA_L3_DSWX-HLS_V1',
        'OPERA_L3_DSWX-S1_V1',
        'OPERA_L3_DIST-ALERT-HLS_V1',
        'OPERA_L3_DIST-ANN-HLS_V1',
        'OPERA_L2_RTC-S1_V1',
        'OPERA_L2_CSLC-S1_V1',
        'OPERA_L3_DISP-S1_V1'
    ]

    # ✅ Parse the bbox argument
    bbox = bbox_type(args.bbox)

    if isinstance(bbox, str):
        # ✅ Create geometry from KML file
        geometry = create_polygon_from_kml(bbox)
        AOI = geometry.bounds  
    else:
        # ✅ Extract bounding box
        lat_min, lat_max, lon_min, lon_max = bbox
        # (minx, miny, maxx, maxy)
        AOI = (lon_min, lat_min, lon_max, lat_max)  

    # Calculate center point
    # (miny + maxy) / 2
    c_lat = (AOI[1] + AOI[3]) / 2 
    # (minx + maxx) / 2 
    c_lon = (AOI[0] + AOI[2]) / 2  

    # ✅ Date ranges for search
    today = date.today()
    one_year_ago = today - relativedelta(months=12)
    StartDate_Recent = one_year_ago.strftime("%Y-%m-%d") + "T00:00:00"
    EndDate_Recent = today.strftime("%Y-%m-%d") + "T23:59:59"

    results_dict = {}
    ng = args.ngr
    print("\n** Available OPERA Products for Selected Area of Interest (AOI) **\n")
    for dataset in opera_datasets:
        print(f"* Searching {dataset} ...")

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            try:
                results, gdf = leafmap.nasa_data_search(
                    short_name=dataset,
                    cloud_hosted=True,
                    bounding_box=AOI,
                    temporal=(StartDate_Recent, EndDate_Recent),
                    return_gdf=True,
                )

                if gdf is not None and not gdf.empty:
                    results = results[-ng:]
                    gdf = gdf[-ng:]
                    print(f"-> Success: {dataset} → {len(gdf)} granule(s) saved.")
                    results_dict[dataset] = {
                        "results": results,
                        "gdf": gdf,
                    }
                    break  
                else:
                    print(f"xxx Attempt {attempt}: No granules for {dataset}.")
            except Exception as e:
                print(f"xxx Attempt {attempt}: Error fetching {dataset}: {str(e)}")

            if attempt < max_attempts:
                time.sleep(2 ** attempt) 
            else:
                print(f"-> Failed to fetch {dataset} after {max_attempts} attempts.")


    # output relevant information (Granule ID, Time Range and Download URL)
    print(f"\n** Relevant information about the {len(gdf)} saved granule(s) per OPERA product :  **")
    for dataset, data in results_dict.items():
        print(f"\n*** Dataset: {dataset} ************************")
        for i, item in enumerate(data["results"], start=1):
            umm = item["umm"]

            granule_id = umm.get("GranuleUR", "N/A")
            temporal = umm.get("TemporalExtent", {})
            start_time = temporal.get("RangeDateTime", {}).get("BeginningDateTime", "N/A")
            end_time = temporal.get("RangeDateTime", {}).get("EndingDateTime", "N/A")

            # Extract download URL 
            download_url = "N/A"
            for url_entry in umm.get("RelatedUrls", []):
                if url_entry.get("Type") == "GET DATA":
                    download_url = url_entry.get("URL", "N/A")
                    break

            print(f"-- Granule #{i}")
            print(f"+ Granule ID: {granule_id}")
            print(f"+ Time Range: {start_time} → {end_time}")
            print(f"+ Download URL: {download_url}")

    
    # export to csv file
    with open("opera_products_metadata.csv", "w", newline="") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Dataset", "Granule ID", "Start Time", "End Time", "Download URL"])

        for dataset, data in results_dict.items():
            for item in data["results"]:
                umm = item["umm"]
                granule_id = umm.get("GranuleUR", "N/A")
                temporal = umm.get("TemporalExtent", {})
                start_time = temporal.get("RangeDateTime", {}).get("BeginningDateTime", "N/A")
                end_time = temporal.get("RangeDateTime", {}).get("EndingDateTime", "N/A")

                download_url = "N/A"
                for url_entry in umm.get("RelatedUrls", []):
                    if url_entry.get("Type") == "GET DATA":
                        download_url = url_entry.get("URL", "N/A")
                        break

                writer.writerow([dataset, granule_id, start_time, end_time, download_url])
    print("\n-> OPERA products metadata successfully saved to opera_granule_metadata.csv")

    return results_dict


def generate_opera_map(results_dict, args):
    import leafmap.foliumap as leafmap  # folium backend
    import webbrowser

    output_path="opera_products_map_1.html"

    # Parse the bbox argument
    bbox = bbox_type(args.bbox)

    if isinstance(bbox, str):
        # Create geometry from KML file
        geometry = create_polygon_from_kml(bbox)
        AOI = geometry.bounds  
    else:
        # Extract bounding box
        lat_min, lat_max, lon_min, lon_max = bbox
        AOI = (lon_min, lat_min, lon_max, lat_max)  

    # Calculate center point
    c_lat = (AOI[1] + AOI[3]) / 2  
    c_lon = (AOI[0] + AOI[2]) / 2  

    m = leafmap.Map(center=(c_lat, c_lon), zoom=3)
    m.add_basemap("Esri.WorldImagery")

    # Generate distinct colors
    colors = hsl_distinct_colors_improved(len(results_dict))
    legend_labels = []
    legend_colors = []

    for i, (dataset, data) in enumerate(results_dict.items()):
        color = colors[i]
        gdf = data.get("gdf")

        if gdf is None or gdf.empty:
            print(f"- Skipping {dataset}: no data.")
            continue

        style = {
            "color": color,
            "fillColor": color,
            "weight": 2,
            "fillOpacity": 0.5,
        }

        try:
            m.add_gdf(gdf, layer_name=dataset, style=style)
            legend_labels.append(dataset)
            legend_colors.append(color)
        except Exception as e:
            print(f"- Could not add {dataset} to map: {e}")

    # Add legend
    m.add_legend(title="OPERA Products", labels=legend_labels, colors=legend_colors)

    # Save and open
    m.save(output_path)
    webbrowser.open(output_path)
    print(f"-> OPERA granules Map successfully saved to {output_path}")


def make_opera_granule_map(results_dict, args):
    """
    Create an interactive map displaying OPERA granules for all datasets with clickable download links.
    """
    import folium
    import geopandas as gpd

    output_file="opera_products_map_2.html"

    # Parse AOI center for initial map centering
    bbox = bbox_type(args.bbox)
    if isinstance(bbox, str):
        geometry = create_polygon_from_kml(bbox)
        AOI = geometry.bounds
    else:
        lat_min, lat_max, lon_min, lon_max = bbox
        AOI = (lon_min, lat_min, lon_max, lat_max)

    center_lat = (AOI[1] + AOI[3]) / 2
    center_lon = (AOI[0] + AOI[2]) / 2

    # Initialize base map
    map_object = folium.Map(location=[center_lat, center_lon], zoom_start=7)
    folium.TileLayer("Esri.WorldImagery").add_to(map_object)

    # Generate distinct colors for layers
    from matplotlib.colors import to_hex
    import matplotlib.pyplot as plt
    cmap = plt.get_cmap("tab20")
    dataset_names = list(results_dict.keys())
    colors = [to_hex(cmap(i % 20)) for i in range(len(dataset_names))]

    legend_entries = []

    for i, (dataset, data) in enumerate(results_dict.items()):
        gdf = data.get("gdf")
        if gdf is None or gdf.empty:
            print(f"⚠️ Skipping {dataset}: empty or missing GeoDataFrame.")
            continue

        gdf = gdf.copy()

        # Add download URL and name for popup
        for idx, row in gdf.iterrows():
            try:
                url = row["meta"]["umm"]["RelatedUrls"][0]["URL"]
                label = row["meta"]["umm"].get("GranuleUR", "OPERA Granule")
            except:
                url = "URL not available"
                label = "OPERA Granule"
            gdf.at[idx, "download_url"] = url
            gdf.at[idx, "GranuleUR"] = label

        color = colors[i]
        style = {
            "color": color,
            "fillColor": color,
            "weight": 2,
            "fillOpacity": 0.5,
        }

        # Add geometries
        folium.GeoJson(
            gdf.__geo_interface__,
            name=dataset,
            style_function=lambda x, style=style: style
        ).add_to(map_object)

        # Add popup markers
        for _, row in gdf.iterrows():
            centroid = row.geometry.centroid
            popup_html = f"""
            <b>{row['GranuleUR']}</b><br>
            <a href="{row['download_url']}" target="_blank">Download Granule</a>
            """
            folium.Marker(
                location=[centroid.y, centroid.x],
                popup=folium.Popup(popup_html, max_width=300),
                icon=folium.Icon(color="blue", icon="cloud-download")
            ).add_to(map_object)

        legend_entries.append((dataset, color))

    # Add layer control
    folium.LayerControl().add_to(map_object)

    # Add legend
    from branca.element import MacroElement
    from jinja2 import Template

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
            <span style="display:inline-block;width:12px;height:12px;background-color:{{ color }};margin-right:6px"></span>{{ name }}
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

    # Save map
    map_object.save(output_file)
    print(f"-> OPERA granules Map successfully saved to {output_file}")




    


