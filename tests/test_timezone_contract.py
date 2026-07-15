"""
Test timezone contract enforcement in tide_prediction module.

These tests verify that the validation checks properly reject timezone-aware
datetimes and accept naive datetimes representing UTC.
"""

import pytest
from datetime import datetime, timezone
import sys
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.tide_prediction import (
    interpolate_tide,
    _find_tide_direction,
    _find_nearest_hilo_label,
)


class TestTimezoneContractEnforcement:
    """Test that tide prediction functions enforce the naive datetime contract."""

    def test_interpolate_tide_rejects_timezone_aware(self):
        """interpolate_tide should raise TypeError for timezone-aware datetime."""
        times = ["2026-06-28T17:00:00", "2026-06-28T18:00:00", "2026-06-28T19:00:00"]
        values = [1.15, 1.23, 1.30]

        # Timezone-aware datetime (UTC)
        dt_aware = datetime(2026, 6, 28, 18, 30, tzinfo=timezone.utc)

        with pytest.raises(TypeError) as excinfo:
            interpolate_tide(times, values, dt_aware)

        assert "naive datetime (UTC)" in str(excinfo.value)
        assert "timezone-aware" in str(excinfo.value)

    def test_interpolate_tide_accepts_naive(self):
        """interpolate_tide should accept naive datetime."""
        times = ["2026-06-28T17:00:00", "2026-06-28T18:00:00", "2026-06-28T19:00:00"]
        values = [1.15, 1.23, 1.30]

        # Naive datetime (represents UTC)
        dt_naive = datetime(2026, 6, 28, 18, 30)

        # Should not raise, should interpolate
        result = interpolate_tide(times, values, dt_naive)

        assert result is not None
        assert isinstance(result, float)
        assert 1.23 < result < 1.30  # Between 18:00 and 19:00 values

    def test_find_tide_direction_rejects_timezone_aware(self):
        """_find_tide_direction should raise TypeError for timezone-aware datetime."""
        times = ["2026-06-28T17:00:00", "2026-06-28T18:00:00", "2026-06-28T19:00:00"]
        values = [1.15, 1.23, 1.30]

        # Timezone-aware datetime
        dt_aware = datetime(2026, 6, 28, 18, 30, tzinfo=timezone.utc)

        with pytest.raises(TypeError) as excinfo:
            _find_tide_direction(times, values, dt_aware)

        assert "naive datetime (UTC)" in str(excinfo.value)
        assert "timezone-aware" in str(excinfo.value)

    def test_find_tide_direction_accepts_naive(self):
        """_find_tide_direction should accept naive datetime."""
        times = ["2026-06-28T17:00:00", "2026-06-28T18:00:00", "2026-06-28T19:00:00"]
        values = [1.15, 1.23, 1.30]

        # Naive datetime
        dt_naive = datetime(2026, 6, 28, 18, 30)

        # Should not raise
        result = _find_tide_direction(times, values, dt_naive)

        assert result in ["rising", "falling", "slack", ""]
        assert result == "rising"  # Values are increasing

    def test_find_nearest_hilo_label_rejects_timezone_aware(self):
        """_find_nearest_hilo_label should raise TypeError for timezone-aware datetime."""
        hilo_predictions = [
            {"t": "2026-06-28 17:45", "type": "L"},
            {"t": "2026-06-28 23:30", "type": "H"},
        ]

        # Timezone-aware datetime
        dt_aware = datetime(2026, 6, 28, 18, 30, tzinfo=timezone.utc)

        with pytest.raises(TypeError) as excinfo:
            _find_nearest_hilo_label(hilo_predictions, dt_aware)

        assert "naive datetime (UTC)" in str(excinfo.value)
        assert "timezone-aware" in str(excinfo.value)

    def test_find_nearest_hilo_label_accepts_naive(self):
        """_find_nearest_hilo_label should accept naive datetime."""
        hilo_predictions = [
            {"t": "2026-06-28 17:45", "type": "L"},
            {"t": "2026-06-28 23:30", "type": "H"},
        ]

        # Naive datetime
        dt_naive = datetime(2026, 6, 28, 18, 30)

        # Should not raise
        result = _find_nearest_hilo_label(hilo_predictions, dt_naive)

        assert result in ["H", "HH", "L", "LL", ""]
        assert result == "L"  # Closer to 17:45 low than 23:30 high


class TestErrorMessageQuality:
    """Test that error messages are helpful for developers."""

    def test_error_message_includes_timezone_info(self):
        """Error message should tell developer which timezone was detected."""
        times = ["2026-06-28T17:00:00", "2026-06-28T18:00:00"]
        values = [1.15, 1.23]
        dt_aware = datetime(2026, 6, 28, 18, 0, tzinfo=timezone.utc)

        with pytest.raises(TypeError) as excinfo:
            interpolate_tide(times, values, dt_aware)

        error_msg = str(excinfo.value)
        assert "UTC" in error_msg  # Should mention the specific timezone
        assert "module docstring" in error_msg  # Should point to documentation

    def test_error_message_is_consistent_across_functions(self):
        """All functions should have consistent error message format."""
        times = ["2026-06-28T17:00:00", "2026-06-28T18:00:00"]
        values = [1.15, 1.23]
        dt_aware = datetime(2026, 6, 28, 18, 0, tzinfo=timezone.utc)

        errors = []

        try:
            interpolate_tide(times, values, dt_aware)
        except TypeError as e:
            errors.append(str(e))

        try:
            _find_tide_direction(times, values, dt_aware)
        except TypeError as e:
            errors.append(str(e))

        try:
            _find_nearest_hilo_label([], dt_aware)
        except TypeError as e:
            errors.append(str(e))

        # All should mention the same key concepts
        for error in errors:
            assert "naive datetime (UTC)" in error
            assert "timezone-aware" in error
            assert "module docstring" in error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
