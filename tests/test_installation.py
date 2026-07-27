from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

RUNTIME_IMPORT_NAMES = [
    "asf_search",
    "bs4",
    "boto3",
    "earthaccess",
    "folium",
    "geopandas",
    "leafmap",
    "lxml",
    "openpyxl",
    "rasterio",
    "shapely",
    "tabulate",
    "timezonefinder",
    "yagmail",
]


def test_runtime_dependencies_are_installed():
    script = (
        "import importlib.util, json\n"
        f"names = {RUNTIME_IMPORT_NAMES!r}\n"
        "missing = [name for name in names if importlib.util.find_spec(name) is None]\n"
        "print(json.dumps(missing))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0, (
        "Dependency validation process failed unexpectedly.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    missing = result.stdout.strip()
    assert missing == "[]", (
        "Missing runtime dependencies. Installation is not complete: " + missing
    )


def test_core_modules_import_with_real_dependencies():
    modules = [
        "next_pass",
        "utils.utils",
        "utils.cloudiness",
        "utils.collection_builder",
        "utils.landsat_pass",
        "utils.nisar_pass",
        "utils.opera_products",
        "utils.plot_maps",
        "utils.sentinel_pass",
    ]
    script = (
        "import importlib, json\n"
        f"modules = {modules!r}\n"
        "failed = {}\n"
        "for module_name in modules:\n"
        "    try:\n"
        "        importlib.import_module(module_name)\n"
        "    except Exception as error:\n"
        "        failed[module_name] = f'{type(error).__name__}: {error}'\n"
        "print(json.dumps(failed, sort_keys=True))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0, (
        "Module import validation process failed unexpectedly.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert (
        result.stdout.strip() == "{}"
    ), f"Core imports failed: {result.stdout.strip()}"


def test_cli_help_starts_successfully():
    result = subprocess.run(
        [sys.executable, str(ROOT / "next_pass.py"), "--help"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0, (
        "CLI help failed to start.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "Find next satellite overpass date." in result.stdout
    assert "--bbox" in result.stdout


def test_programmatic_entrypoint_imports_and_parser_builds():
    script = (
        "import next_pass\n"
        "parser = next_pass.create_parser()\n"
        "args = parser.parse_args(['-b', '34.2', '-118.17'])\n"
        "print(args.bbox)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 0, (
        "Programmatic parser validation failed.\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "['34.2', '-118.17']" in result.stdout
