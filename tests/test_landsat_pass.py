import sys
import types
import unittest
from datetime import date


def _install_test_stubs() -> None:
    """Stub optional GIS imports so schedule tests can run in a lean env."""
    shapely_module = types.ModuleType("shapely")
    shapely_geometry_module = types.ModuleType("shapely.geometry")
    shapely_geometry_base_module = types.ModuleType("shapely.geometry.base")
    shapely_ops_module = types.ModuleType("shapely.ops")
    tabulate_module = types.ModuleType("tabulate")

    class _DummyGeometry:
        pass

    shapely_geometry_module.Point = _DummyGeometry
    shapely_geometry_module.Polygon = _DummyGeometry
    shapely_geometry_base_module.BaseGeometry = _DummyGeometry
    shapely_ops_module.unary_union = lambda geometries: geometries
    tabulate_module.tabulate = lambda *args, **kwargs: ""

    utils_utils_module = types.ModuleType("utils.utils")
    utils_utils_module.arcgis_to_polygon = lambda geometry: geometry

    sys.modules.setdefault("shapely", shapely_module)
    sys.modules.setdefault("shapely.geometry", shapely_geometry_module)
    sys.modules.setdefault("shapely.geometry.base", shapely_geometry_base_module)
    sys.modules.setdefault("shapely.ops", shapely_ops_module)
    sys.modules.setdefault("tabulate", tabulate_module)
    sys.modules.setdefault("utils.utils", utils_utils_module)


_install_test_stubs()

from utils.landsat_pass import (  # noqa: E402
    LandsatScheduleSource,
    find_next_landsat_pass,
)


class FindNextLandsatPassTests(unittest.TestCase):
    def test_modern_schedule_returns_2026_dates(self) -> None:
        schedule_source = LandsatScheduleSource(
            source="modern",
            cycle_sequence=list(range(1, 17)),
            mission_cycle_paths={
                "landsat_8": {8: {101}},
                "landsat_9": {12: {101}},
            },
        )

        next_passes, warnings = find_next_landsat_pass(
            path=101,
            n_day_past=13,
            schedule_source=schedule_source,
            num_passes=3,
            today=date(2026, 3, 19),
        )

        self.assertEqual(warnings, [])
        self.assertEqual(
            next_passes["landsat_8"],
            ["03/07/2026", "03/23/2026", "04/08/2026"],
        )
        self.assertEqual(
            next_passes["landsat_9"],
            ["03/11/2026", "03/27/2026", "04/12/2026"],
        )

    def test_legacy_fallback_surfaces_stale_warning_and_last_dates(self) -> None:
        schedule_source = LandsatScheduleSource(
            source="legacy",
            legacy_cycles={
                "landsat_8": {
                    "12/15/2025": {"path": "101,110"},
                    "12/31/2025": {"path": "101,111"},
                },
                "landsat_9": {
                    "12/07/2025": {"path": "101,112"},
                    "12/31/2025": {"path": "101,113"},
                },
            },
            latest_legacy_date=date(2025, 12, 31),
        )

        next_passes, warnings = find_next_landsat_pass(
            path=101,
            n_day_past=13,
            schedule_source=schedule_source,
            num_passes=5,
            today=date(2026, 3, 19),
        )

        self.assertEqual(
            next_passes["landsat_8"],
            ["12/15/2025", "12/31/2025"],
        )
        self.assertEqual(
            next_passes["landsat_9"],
            ["12/07/2025", "12/31/2025"],
        )
        self.assertEqual(len(warnings), 1)
        self.assertIn("stale through 12/31/2025", warnings[0])


if __name__ == "__main__":
    unittest.main()
