#!/usr/bin/env python3
"""
Test timezone validation in tide prediction system.

This test verifies that the timezone validation enhancement works correctly:
1. UTC datetimes pass through without warnings
2. Non-UTC datetimes are converted to UTC with warnings
3. Naive datetimes trigger warnings but are assumed to be UTC
"""

import logging
from datetime import datetime, timezone, timedelta
from io import StringIO
from shapely.geometry import Point

# Set up logging to capture warnings
log_stream = StringIO()
handler = logging.StreamHandler(log_stream)
handler.setLevel(logging.WARNING)
formatter = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)

logger = logging.getLogger("tide_prediction")
logger.addHandler(handler)
logger.setLevel(logging.WARNING)

# Import after setting up logging  # noqa: E402
from utils.tide_prediction import make_get_tide_for_row  # noqa: E402


def test_utc_datetime():
    """Test that UTC-aware datetimes pass through without warnings."""
    print("Test 1: UTC-aware datetime (should pass without warning)")
    log_stream.truncate(0)
    log_stream.seek(0)

    # Create a mock row with UTC datetime (dict-like access)
    class MockRow:
        def __init__(self):
            dt = datetime(2026, 6, 28, 18, 0, 0, tzinfo=timezone.utc)
            self._data = {"begin_date": dt}

        def __getitem__(self, key):
            return self._data[key]

    # Create tide function with mock geometry and empty stations
    aoi = Point(-118.0, 34.0).buffer(0.1)
    tide_func = make_get_tide_for_row(aoi, [])

    # This should not produce warnings (though it will fail with no stations)
    try:
        tide_func(MockRow())
    except Exception:
        pass  # Expected to fail with no stations

    warnings = log_stream.getvalue()
    assert "timezone" not in warnings.lower() and "naive" not in warnings.lower(), \
        f"Unexpected warning: {warnings}"
    print("  ✅ PASS: No timezone warnings")


def test_non_utc_datetime():
    """Test that non-UTC datetimes are converted with warnings."""
    print("\nTest 2: Non-UTC datetime (should trigger conversion warning)")
    log_stream.truncate(0)
    log_stream.seek(0)

    # Create a mock row with Pacific timezone datetime
    class MockRow:
        def __init__(self):
            # PST is UTC-8
            pst = timezone(timedelta(hours=-8))
            self._data = {
                "begin_date": datetime(2026, 6, 28, 10, 0, 0, tzinfo=pst)
            }

        def __getitem__(self, key):
            return self._data[key]

    # Create tide function
    aoi = Point(-118.0, 34.0).buffer(0.1)
    tide_func = make_get_tide_for_row(aoi, [])

    try:
        tide_func(MockRow())
    except Exception:
        pass  # Expected to fail with no stations

    warnings = log_stream.getvalue()
    assert "Non-UTC datetime detected" in warnings, \
        f"Expected warning not found. Got: {warnings}"
    print(f"  ✅ PASS: Got expected warning: {warnings.strip()}")


def test_naive_datetime():
    """Test that naive datetimes trigger warnings."""
    print("\nTest 3: Naive datetime (should trigger assumption warning)")
    log_stream.truncate(0)
    log_stream.seek(0)

    # Create a mock row with naive datetime (no timezone)
    class MockRow:
        def __init__(self):
            # No tzinfo
            self._data = {"begin_date": datetime(2026, 6, 28, 18, 0, 0)}

        def __getitem__(self, key):
            return self._data[key]

    # Create tide function
    aoi = Point(-118.0, 34.0).buffer(0.1)
    tide_func = make_get_tide_for_row(aoi, [])

    try:
        tide_func(MockRow())
    except Exception:
        pass  # Expected to fail with no stations

    warnings = log_stream.getvalue()
    assert "Naive datetime detected" in warnings, \
        f"Expected warning not found. Got: {warnings}"
    print(f"  ✅ PASS: Got expected warning: {warnings.strip()}")


def test_datetime_list():
    """Test that lists of datetimes are handled correctly."""
    print("\nTest 4: List of mixed datetimes (should handle all correctly)")
    log_stream.truncate(0)
    log_stream.seek(0)

    # Create a mock row with list of different datetime types
    class MockRow:
        def __init__(self):
            pst = timezone(timedelta(hours=-8))
            self._data = {
                "begin_date": [
                    datetime(2026, 6, 28, 18, 0, 0, tzinfo=timezone.utc),
                    datetime(2026, 6, 29, 10, 0, 0, tzinfo=pst),
                    datetime(2026, 6, 30, 18, 0, 0),  # Naive
                ]
            }

        def __getitem__(self, key):
            return self._data[key]

    # Create tide function
    aoi = Point(-118.0, 34.0).buffer(0.1)
    tide_func = make_get_tide_for_row(aoi, [])

    try:
        tide_func(MockRow())
    except Exception:
        pass  # Expected to fail with no stations

    warnings = log_stream.getvalue()
    has_non_utc_warning = "Non-UTC datetime detected" in warnings
    has_naive_warning = "Naive datetime detected" in warnings

    assert has_non_utc_warning and has_naive_warning, (
        f"Missing expected warnings. Non-UTC: {has_non_utc_warning}, "
        f"Naive: {has_naive_warning}. Got: {warnings}"
    )
    print("  ✅ PASS: Got both expected warnings")
    print(f"     {warnings.strip()}")


if __name__ == "__main__":
    print("=" * 60)
    print("Timezone Validation Tests")
    print("=" * 60)

    tests = [
        test_utc_datetime,
        test_non_utc_datetime,
        test_naive_datetime,
        test_datetime_list,
    ]

    results = []
    for test in tests:
        try:
            test()
            results.append(True)
            print(f"✓ {test.__name__}")
        except AssertionError as e:
            results.append(False)
            print(f"✗ {test.__name__}: {e}")
        except Exception as e:
            results.append(False)
            print(f"✗ {test.__name__} (unexpected error): {e}")

    print("\n" + "=" * 60)
    print(f"Results: {sum(results)}/{len(results)} tests passed")
    print("=" * 60)

    if all(results):
        print("\n✅ All tests passed!")
        exit(0)

    print("\n❌ Some tests failed!")
    exit(1)
