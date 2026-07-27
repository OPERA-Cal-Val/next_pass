# NEXT PASS

[![Test](https://github.com/OPERA-Cal-Val/next_pass/actions/workflows/test.yml/badge.svg)](https://github.com/OPERA-Cal-Val/next_pass/actions/workflows/test.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Predict the **next satellite overpass** for a point, bounding box, or KML AOI — supporting **Sentinel‑1**, **Sentinel‑2**, **Landsat‑8**, **Landsat‑9**, and **NISAR**.
Optionally filter by **OPERA product families**, **estimate cloudiness** for the upcoming pass, and **email** results.

---

## Highlights

- **Satellites**: Sentinel-1, Sentinel-2, Landsat-8, Landsat-9, NISAR
- **AOI inputs**: **Point** (lat, lon), **SNWE** bounding box, or **KML** polygon
- **OPERA product filters**: limit search to product families (e.g., `DSWX-HLS_V1`, `DSWX-S1_V1`)
- **Cloudiness prediction**: for next S1/S2 overpasses (`-c`)
- **Email** notifications: send S1/S2 results via SMTP (`--email`)
- **Examples included**: `examples/`

---

## Repo layout

```
next_pass/
├─ examples/          # Jupyter notebooks and sample workflows
├─ utils/             # Core helpers (Sentinel/Landsat passes, cloudiness, OPERA products, plotting, I/O)
├─ next_pass.py       # CLI entry point (used by the `next-pass` console script)
├─ environment.yml    # Conda/mamba environment for development and notebooks
├─ requirements.txt   # Runtime dependency list (used by pyproject / pip)
├─ pyproject.toml     # Modern packaging metadata (setuptools backend)
├─ setup.py           # Legacy shim for older tooling
└─ LICENSE            # Apache-2.0
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
mamba env create -f environment.yml
mamba activate next_pass
```
or with conda

```bash
conda env create -f environment.yml
conda activate next_pass
```

Alternatively, install the runtime dependencies directly:

```bash
conda install -c conda-forge --yes --file requirements.txt
```

Install the package (optional but recommended)
```bash
pip install -e .
```

---

## Usage

The main entry point is `next-pass`. Choose one AOI input form and add options as needed.

### 1) Point (lat, lon)

```bash
next-pass -b 34.20 -118.17
```

### 2) Bounding box (SNWE = South North West East)

```bash
next-pass -b 34.15 34.25 -118.20 -118.15
```

### 3) KML file (polygon)

```bash
next-pass -b /path/to/aoi.kml
```

### 4) WKT coordinates (polygon or point)

```bash
next-pass -b "POLYGON ((-123.1 47.33, -123.16 47.28, -123.33 47.33, -123.25 47.34, -123.19 47.32, -123.15 47.35, -123.1 47.33))"
```

### 5) link to .geojson file (online or local)

```bash
next-pass -b "https://api.weather.gov/alerts/urn:oid:2.49.0.1.840.0.dc03b8d7d3aa06ec27afb812ac02d6afa8b5f0ce.002.1"
```
```bash
next-pass -b AOI_from_url.geojson
```

### Options

- **Satellite** subset (e.g., S1 only) and **email** the results:

  ```bash
  next-pass -b 50 52 -102 -100 -s sentinel-1 --email
  ```

- **NISAR** overpasses from the official NASA observation-plan KMZ:

  ```bash
  next-pass -b 34.15 34.25 -118.20 -118.15 -s nisar
  ```

- **Restrict OPERA products** considered during the search (space‑separated list):

  ```bash
  next-pass -b 29 31 -100 -97 -p DSWX-HLS_V1 DSWX-S1_V1
  ```

- **Predict cloudiness** for the next S1/S2 overpasses (adds a cloud estimate column):

  ```bash
  next-pass -b 29 31 -100 -97 -p DSWX-HLS_V1 DSWX-S1_V1 -c
  ```

- **Generate old OPERA products** for a previous event date (YYYY-MM-DD):

  ```bash
  next-pass -b 17.32 18.80 -78.61 -75.58 -f opera_search -d 2025-10-01
  ```

- **Generate OPERA Products DRCS map** using a UTC event date in format YYYY-MM-DDTHH:MM
  (Please consider replacing the date in the example with a recent event date):

  ```bash
  next-pass -b 17.32 18.80 -78.61 -75.58 -g 2025-11-18T01:00
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

For development setup and code quality standards, see [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

Apache‑2.0 — see [`LICENSE`](LICENSE).

---
