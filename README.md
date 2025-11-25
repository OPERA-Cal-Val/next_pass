# NEXT PASS

Predict the **next satellite overpass** for a point, bounding box, or KML AOI — supporting **Sentinel‑1**, **Sentinel‑2**, **Landsat‑8**, and **Landsat‑9**.  
Optionally filter by **OPERA product families**, **estimate cloudiness** for the upcoming pass, and **email** results.

---

## Highlights

- **Satellites**: Sentinel-1, Senteinel-2, Landsat-8, Landsat-9
- **AOI inputs**: **Point** (lat, lon), **SNWE** bounding box, or **KML** polygon
- **OPERA product filters**: limit search to product families (e.g., `DSWX-HLS_V1`, `DSWX-S1_V1`)
- **Cloudiness prediction**: for next S1/S2 overpasses (`-c`)
- **Email** notifications: send S1/S2 results via SMTP (`--email`)
- **Examples included**: `examples/`

---

## Repo layout

```
next_pass/
├─ examples/                 # Jupyter notebook(s) and sample runs
├─ cloudiness.py             # Cloud estimate helpers for S1/S2
├─ collection_builder.py     # Build search collections
├─ landsat_pass.py           # Landsat pass utilities
├─ sentinel_pass.py          # Sentinel-1/2 pass utilities
├─ opera_products.py         # OPERA product family helpers
├─ next_pass.py              # CLI entry point
├─ utils.py                  # Shared helpers (I/O, geometry, etc.)
├─ plot_maps.py              # Optional map visualization utilities
├─ environment.yml           # Mamba/conda environment
├─ requirements.txt          # Alternative dependency list
├─ pyproject.toml / setup.py # Packaging metadata
└─ LICENSE                   # Apache-2.0
```

---

## Installation

Clone the repository:

```bash
git clone https://github.com/OPERA-Cal-Val/next_pass.git
cd next_pass
```

Create a fresh environment **(recommended)**:

```bash
conda env create -f environment.yml
conda activate next_pass
```

Or install into an existing environment:

```bash
mamba install -c conda-forge --yes --file requirements.txt
```

> Tip: If you prefer `pip`, you can also do `pip install -e .` after activating a suitable environment.

---

## Usage

The main entry point is `next_pass.py`. Choose one AOI input form and add options as needed.

### 1) Point (lat, lon)

```bash
python next_pass.py -b 34.20 -118.17
```

### 2) Bounding box (SNWE = South North West East)

```bash
python next_pass.py -b 34.15 34.25 -118.20 -118.15
```

### 3) KML file (polygon)

```bash
python next_pass.py -b /path/to/aoi.kml
```

### Options

- **Satellite** subset (e.g., S1 only) and **email** the results:

  ```bash
  python next_pass.py -b 50 52 -102 -100 -s sentinel-1 --email
  ```

- **Restrict OPERA products** considered during the search (space‑separated list):

  ```bash
  python next_pass.py -b 29 31 -100 -97 -p DSWX-HLS_V1 DSWX-S1_V1
  ```

- **Predict cloudiness** for the next S1/S2 overpasses (adds a cloud estimate column):

  ```bash
  python next_pass.py -b 29 31 -100 -97 -p DSWX-HLS_V1 DSWX-S1_V1 -c
  ```

- **Generate old OPERA products** for a previous event date (YYYY-MM-DD):
  
  ```bash
  python next_pass.py -b 17.32 18.80 -78.61 -75.58 -f opera_search -d 2025-10-01
  ```

- **Generate OPERA Products DRCS map** using a UTC event date in format YYYY-MM-DDTHH:MM 
  (Please consider replacing the date in the example with a recent event date):

  ```bash
  python next_pass.py -b 17.32 18.80 -78.61 -75.58 -g 2025-11-18T01:00
  ```

> Use `-h/--help` to see all flags and defaults.

---

<!-- ## Example notebook

A quick‑start notebook lives under `examples/`:

- **`examples/Run_next_pass.ipynb`** – step‑by‑step walkthrough for common scenarios (point, SNWE bbox, KML).

Open it in Jupyter after activating the environment:

```bash
jupyter lab examples/Run_next_pass.ipynb
``` -->

## Contributing

Issues and pull requests are welcome! If adding a new satellite, product family, or IO backend, please include a small example and a test (if applicable).

---

## License

Apache‑2.0 — see [`LICENSE`](LICENSE).

---
