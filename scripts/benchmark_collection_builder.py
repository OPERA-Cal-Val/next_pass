#!/usr/bin/env python3
"""Before/after benchmark for the parallel collection-building pipeline.

Clears the local scratch cache and builds the Sentinel-1 + Sentinel-2
acquisition-plan collections twice against the real ESA endpoints: once with
``NEXTPASS_MAX_WORKERS=1`` (a sequential-equivalent baseline, since a single
worker serializes every download/parse through the same code path used
before this pipeline was parallelized) and once with the default worker
count. Prints wall-clock time and the resulting speedup.

Not a pytest test: timing assertions on live network calls are inherently
flaky in CI. Run manually to sanity-check the parallel speedup after changes
to utils/collection_builder.py or utils/sentinel_pass.py:

    python scripts/benchmark_collection_builder.py
"""

import logging
import os
import shutil
import time
from pathlib import Path

from next_pass.sentinel_pass import create_s1_collection_plan, create_s2_collection_plan

logging.basicConfig(level=logging.WARNING)

SCRATCH_DIR = Path.cwd() / "scratch"
N_DAY_PAST = 13


def _clear_scratch() -> None:
    if SCRATCH_DIR.exists():
        shutil.rmtree(SCRATCH_DIR)


def _run_once() -> float:
    start = time.perf_counter()
    create_s1_collection_plan(N_DAY_PAST)
    create_s2_collection_plan(N_DAY_PAST)
    return time.perf_counter() - start


def main() -> None:
    _clear_scratch()
    os.environ["NEXTPASS_MAX_WORKERS"] = "1"
    sequential_time = _run_once()
    num_kml_files = sum(1 for _ in SCRATCH_DIR.glob("*.kml"))

    _clear_scratch()
    os.environ["NEXTPASS_MAX_WORKERS"] = "8"
    concurrent_time = _run_once()

    print(f"Workload: {num_kml_files} KML files (Sentinel-1 + Sentinel-2 plans)")
    print(f"Sequential (NEXTPASS_MAX_WORKERS=1): {sequential_time:.2f}s")
    print(f"Concurrent (NEXTPASS_MAX_WORKERS=8): {concurrent_time:.2f}s")
    print(f"Speedup: {sequential_time / concurrent_time:.2f}x")


if __name__ == "__main__":
    main()
