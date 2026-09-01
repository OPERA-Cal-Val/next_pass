# NEXT PASS

[![PyPI version](https://img.shields.io/pypi/v/next-pass?logo=pypi&logoColor=white&label=PyPI)](https://pypi.org/project/next-pass/)
[![Python versions](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue?logo=python&logoColor=white)](https://pypi.org/project/next-pass/)
[![CI](https://github.com/OPERA-Cal-Val/next_pass/actions/workflows/ci.yml/badge.svg)](https://github.com/OPERA-Cal-Val/next_pass/actions/workflows/ci.yml)
[![Overpass map](https://img.shields.io/badge/overpass%20map-live-1baf7a?logo=leaflet&logoColor=white)](https://opera-cal-val.github.io/next_pass/)
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
- **Interactive overpass map**: a daily-updated GitHub Pages map of the coming week's passes, plus a local viewer (`next-pass-viewer`)
- **Examples included**: `examples/`

---

## Repo layout

```
next_pass/
├─ examples/            # Jupyter notebooks and sample workflows
├─ src/next_pass/       # The package: CLI, per-sensor pass modules, schedule + viewer
│  └─ viewer_assets/    # Static HTML for the interactive overpass map
├─ tests/               # Pytest suite
├─ pyproject.toml       # Packaging metadata + pixi environments + ruff config
└─ LICENSE              # Apache-2.0
```

---

## Installation

### Pixi (recommended for development)

Install [pixi](https://pixi.sh/latest/#installation), then:

```bash
git clone https://github.com/OPERA-Cal-Val/next_pass.git
cd next_pass
pixi install
pixi run next-pass --help
```

For JupyterLab (with `jupyter-collaboration` for real-time collaborative editing):

```bash
pixi run jupyter lab
```

### PyPI

```bash
pip install next-pass
```

---

## Interactive overpass map

A GitHub Actions cron job runs daily: it precomputes the coming week of passes for
all supported sensors into a GeoParquet file and publishes it, together with an
interactive map, to the `gh-pages` branch (GitHub Pages). Open the project's
GitHub Pages site, drop a point or draw a box, pick a date range, and see which
sensors will pass over that AOI — built for rapidly triaging which products will
be available over a disaster-response AOI.

The same map runs locally against any schedule file:

```bash
pixi run next-pass-schedule --days 7 --output upcoming_passes.parquet
pixi run next-pass-viewer --data upcoming_passes.parquet
```

`next-pass-schedule` writes a GeoParquet (plus a `.meta.json` sidecar) with one
row per planned pass: `sensor`, `platform`, `mode`, `orbit_direction`, `track`,
`begin_time`/`end_time` (UTC), `time_is_estimated`, and the footprint `geometry`.
Sentinel times come from ESA acquisition plans; Landsat and NISAR times are
local-solar-time estimates (roughly ±40 min for NISAR frames, up to ±1–2 h for
Landsat path-level swaths — run the `next-pass` CLI for precise times over an AOI).

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
