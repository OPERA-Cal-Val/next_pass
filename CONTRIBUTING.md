# Contributing to next_pass

## Repo layout

```
next_pass/
├─ src/next_pass/          # The package
│  ├─ __init__.py          # `next-pass` CLI (argparse) + programmatic entry points
│  ├─ sentinel_pass.py     # Per-sensor pass logic
│  ├─ landsat_pass.py
│  ├─ nisar_pass.py
│  ├─ opera_products.py    # OPERA product search
│  ├─ cloudiness.py        # Open-Meteo cloud estimates
│  ├─ tide_prediction.py   # NOAA tide predictions
│  ├─ plot_maps.py         # Folium/leafmap output for CLI runs
│  ├─ schedule.py          # `next-pass-schedule`: global GeoParquet precompute
│  ├─ viewer.py            # `next-pass-viewer`: local server for the map
│  └─ viewer_assets/       # index.html — the interactive map (shipped in the wheel)
├─ tests/                  # Pytest suite
├─ examples/               # Notebooks and sample AOIs
└─ pyproject.toml          # Packaging + pixi environments + ruff config
```

Everything is configured in `pyproject.toml` — there is no `setup.py`,
`requirements.txt`, `environment.yml`, or `pytest.ini`.

## Development Setup

### 1. Install dependencies

Install [pixi](https://pixi.sh/latest/#installation), then:

```bash
pixi install
```

This creates the development environment and installs `next-pass` in editable
mode, so the `next-pass`, `next-pass-schedule`, and `next-pass-viewer` CLIs are
immediately available via `pixi run`.

### 2. Setup pre-commit hooks

```bash
# Install pre-commit hooks
pixi run pre-commit install

# Run hooks on all files (optional, to check current state)
pixi run pre-commit run --all-files
```

The pre-commit hooks will automatically run on every commit and check:
- Formatting and linting (ruff — replaces black, isort, and flake8)
- Trailing whitespace
- YAML/TOML syntax
- Large file additions
- Debug statements

### 3. Run tests

```bash
pixi run test
# or with coverage
pixi run cov
```

Tests marked `integration` hit live APIs and are excluded from the default run;
they run nightly in CI. To run them locally:

```bash
pixi run pytest tests -m integration
```

### 4. Lint and format

```bash
pixi run lint    # ruff check
pixi run fix     # ruff check --fix
pixi run format  # ruff format
```

## Pixi environments

| Environment | Python | Purpose |
|---|---|---|
| `default` | 3.13 | Everything: the package, JupyterLab, pytest, ruff. The only one you need locally. |
| `py311`, `py312` | 3.11, 3.12 | CI matrix only — prove the package works on the older supported Pythons. |
| `lint` | none | Just ruff. Used by the CI lint job so it doesn't build the geospatial stack. |

`pixi install` and `pixi run` only touch `default`. Any `-e` flag materializes
that environment on disk (~1 GB for the geospatial stack), **including
read-only-looking commands** like `pixi list -e py312`. Reproduce a matrix
failure with `pixi run -e py312 pytest tests`, then reclaim the space with
`pixi clean -e py312` (leaves `default` intact).

The package supports Python 3.11+ (3.10 is at end-of-life). Each environment has
its own solve group, so older Pythons may resolve older dependencies — e.g.
`py311` gets `earthaccess 0.17` / `rasterio 1.4` while `default` gets the newest.
When a pin blocks an older environment, loosen the *lower* bound and let the
solver pick per-env versions rather than forcing everything to one version. To
add support for a new Python release, add a `[tool.pixi.feature.pyXYZ]` block,
point `default` at it, give the previously-newest version its own environment
entry, add it to the CI matrix and the `Programming Language :: Python :: 3.X`
classifiers, then `pixi lock` and run the suite. A solve failure at `pixi lock`
means conda-forge doesn't have the stack yet — don't claim support without it.

Keep `requires-python` and the feature list in sync; CI is what enforces the
claim.

## The overpass map

A daily GitHub Actions cron (`.github/workflows/overpass-map.yml`, 04:17 UTC)
precomputes the coming week of passes for every sensor and publishes an
interactive map to the `gh-pages` branch. It also re-runs on any push to `main`
that touches `schedule.py` or `viewer_assets/`, so a UI change goes live without
waiting for the next cron, and it can be triggered by hand from the Actions tab
(`workflow_dispatch`) — do that to verify a change end-to-end before relying on
the nightly run. Two pieces make it up:

- **`src/next_pass/schedule.py`** builds the data. It reuses the per-sensor
  collection builders **before** they intersect with an AOI, so the output is
  global, and normalizes everything into one GeoParquet: `sensor`, `platform`,
  `mode`, `orbit_direction`, `track`, `begin_time`/`end_time` (UTC),
  `time_is_estimated`, `geometry`. A `.meta.json` sidecar records the build time
  and per-sensor counts.
- **`src/next_pass/viewer_assets/index.html`** reads that parquet directly in the
  browser (hyparquet + a small WKB decoder) and does the AOI intersection
  client-side with turf. `src/next_pass/viewer.py` serves it locally.

Iterate on either one without touching CI:

```bash
pixi run next-pass-schedule --days 7 --output upcoming_passes.parquet
pixi run next-pass-viewer --data upcoming_passes.parquet
```

A full build takes several minutes (it downloads the ESA plan KMLs, a ~17 MB
NISAR KMZ, and the global WRS-2 grid). While working on the HTML, build the
parquet once and keep re-running the viewer against it — and pass `--sensors` to
narrow a rebuild, e.g. `--sensors nisar`.

`next-pass-schedule` wipes `./scratch` on each run so a stale cached plan can't
silently survive; pass `--no-refresh` to reuse what's already downloaded.

Notes for anyone changing this:
- `viewer_assets/*` ships in the wheel via `[tool.setuptools.package-data]`. A
  new asset file needs no config change, but a new *directory* does.
- The map reads the parquet with `utf8: false`; without it hyparquet decodes the
  binary geometry column as a string and every footprint fails to parse.
- Sentinel times come from the ESA plans and are exact. Landsat and NISAR times
  are local-solar-time estimates computed from each footprint's own centroid, and
  the UI must keep labeling them as estimates.
- The map's sensor colors come from a validated colorblind-safe palette
  (`SENSORS` in `index.html`). Add new hues in slot order rather than picking
  something that looks nice next to the others.

**One-time repo setup**: after the workflow's first successful run, point GitHub
Pages at the `gh-pages` branch (Settings → Pages → Source: Deploy from a branch →
`gh-pages` / root). Until that's done the workflow succeeds but the site isn't
served. The workflow publishes with `force_orphan: true`, so the branch stays a
single commit and the daily parquet never accumulates in history.

## Pull Request Guidelines

1. Ensure all tests pass
2. Add tests for new features
3. Update documentation as needed
4. Follow existing code style (enforced by pre-commit)
5. Keep commits focused and atomic
6. Write clear commit messages

CI runs on every PR: the test matrix (`py311`, `py312`, and `default` on Linux,
`default` on macOS), a ruff check/format check, and a `pip-audit` dependency scan.

## Adding New Satellites

When adding support for a new satellite:
1. Create a new module in `src/next_pass/` (e.g., `src/next_pass/new_satellite_pass.py`)
2. Add corresponding tests in `tests/test_new_satellite_pass.py`
3. Update CLI options in `src/next_pass/__init__.py`
4. Add a `build_*_schedule` function in `src/next_pass/schedule.py` returning the
   normalized columns above, and wire it into `build_schedule` and `ALL_SENSORS`
5. Add the sensor to the `SENSORS` list in `viewer_assets/index.html` so it gets a
   color, a legend row, and a filter toggle
6. Add examples to README.md
7. Include small test fixtures if needed

Prefer AOI-free builders: anything that needs a user AOI can't feed the global
precompute. Keep the plan-parsing step separate from the intersection step.

## Release Process

Releases are automated via GitHub Actions when a version tag is pushed.

### Creating a Release

1. **Update version in `pyproject.toml`**:
   ```toml
   version = "0.2.0"
   ```

2. **Commit the version bump**:
   ```bash
   git add pyproject.toml
   git commit -m "Bump version to 0.2.0"
   ```

3. **Create and push a tag**:
   ```bash
   git tag -a v0.2.0 -m "Release v0.2.0"
   git push origin main
   git push origin v0.2.0
   ```

4. **Automated workflow**:
   - Builds distribution packages (wheel + sdist)
   - Publishes to PyPI (requires `PYPI_API_TOKEN` secret)
   - Creates GitHub release with auto-generated changelog

Check the built wheel locally before tagging if you changed packaging:

```bash
pixi run python -m pip wheel . --no-deps -w /tmp/dist
```

Confirm it contains `next_pass/viewer_assets/index.html` and all three console
scripts.

### Version Numbering

Follow [Semantic Versioning](https://semver.org/):
- **MAJOR** (1.0.0): Breaking API changes
- **MINOR** (0.2.0): New features, backward compatible
- **PATCH** (0.1.1): Bug fixes, backward compatible
