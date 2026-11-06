#!/usr/bin/env python3
"""Before/after benchmark for the parallel collection-building pipeline.

Builds the Sentinel-1 + Sentinel-2 acquisition-plan collections twice in
isolated temporary directories against the real ESA endpoints: once with
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
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from next_pass import collection_builder, sentinel_pass

logging.basicConfig(level=logging.WARNING)

N_DAY_PAST = 13


def _run_once(max_workers: int) -> tuple[float, int]:
    with TemporaryDirectory(prefix="next-pass-benchmark-") as directory:
        scratch_dir = Path(directory)
        with (
            patch.object(collection_builder, "SCRATCH_DIR", scratch_dir),
            patch.dict(os.environ, {"NEXTPASS_MAX_WORKERS": str(max_workers)}),
        ):
            start = time.perf_counter()
            sentinel_pass.create_s1_collection_plan(N_DAY_PAST)
            sentinel_pass.create_s2_collection_plan(N_DAY_PAST)
            elapsed = time.perf_counter() - start
            num_kml_files = sum(1 for _ in scratch_dir.glob("*.kml"))
    return elapsed, num_kml_files


def main() -> None:
    sequential_time, num_kml_files = _run_once(max_workers=1)
    concurrent_time, _ = _run_once(max_workers=8)

    print(f"Workload: {num_kml_files} KML files (Sentinel-1 + Sentinel-2 plans)")
    print(f"Sequential (NEXTPASS_MAX_WORKERS=1): {sequential_time:.2f}s")
    print(f"Concurrent (NEXTPASS_MAX_WORKERS=8): {concurrent_time:.2f}s")
    print(f"Speedup: {sequential_time / concurrent_time:.2f}x")


if __name__ == "__main__":
    main()
