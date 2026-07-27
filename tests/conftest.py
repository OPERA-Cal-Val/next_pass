import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _install_numpy_stub() -> None:
    if importlib.util.find_spec("numpy") is not None:
        return

    numpy_module = types.ModuleType("numpy")

    def arange(start, stop=None, step=1):
        if stop is None:
            stop = start
            start = 0
        values = []
        current = start
        while current < stop:
            values.append(current)
            current += step
        return values

    def isin(values, candidates):
        return [value in candidates for value in values]

    def any_(values):
        return any(values)

    def count_nonzero(values):
        return sum(1 for value in values if value)

    def isscalar(value):
        return isinstance(value, (int, float, str, bool))

    numpy_module.arange = arange
    numpy_module.isin = isin
    numpy_module.any = any_
    numpy_module.count_nonzero = count_nonzero
    numpy_module.isscalar = isscalar
    sys.modules["numpy"] = numpy_module


def _install_shapely_stub() -> None:
    if importlib.util.find_spec("shapely") is not None:
        return

    shapely_module = types.ModuleType("shapely")
    shapely_geometry_module = types.ModuleType("shapely.geometry")
    shapely_geometry_base_module = types.ModuleType("shapely.geometry.base")
    shapely_ops_module = types.ModuleType("shapely.ops")

    class BaseGeometry:
        geom_type = "Geometry"

        @property
        def is_valid(self):
            return True

        @property
        def is_empty(self):
            return False

        def buffer(self, _distance):
            return self

        def intersects(self, _other):
            return True

        def intersection(self, _other):
            return self

    class Point(BaseGeometry):
        geom_type = "Point"

        def __init__(self, x, y):
            self.x = x
            self.y = y

        @property
        def bounds(self):
            return (self.x, self.y, self.x, self.y)

        @property
        def centroid(self):
            return self

        @property
        def area(self):
            return 0.0

        def contains(self, other):
            return self.x == other.x and self.y == other.y

        @property
        def wkt(self):
            return f"POINT ({self.x} {self.y})"

    class LinearRing:
        def __init__(self, coords):
            self.coords = list(coords)

        def __iter__(self):
            return iter(self.coords)

    class Polygon(BaseGeometry):
        geom_type = "Polygon"

        def __init__(self, coords):
            if isinstance(coords, LinearRing):
                coords = coords.coords
            self.coords = list(coords)

        @property
        def exterior(self):
            return types.SimpleNamespace(coords=self.coords)

        @property
        def bounds(self):
            xs = [coord[0] for coord in self.coords]
            ys = [coord[1] for coord in self.coords]
            return (min(xs), min(ys), max(xs), max(ys))

        @property
        def centroid(self):
            xs = [coord[0] for coord in self.coords]
            ys = [coord[1] for coord in self.coords]
            return Point(sum(xs) / len(xs), sum(ys) / len(ys))

        @property
        def area(self):
            minx, miny, maxx, maxy = self.bounds
            return abs((maxx - minx) * (maxy - miny)) or 1.0

        def contains(self, point):
            minx, miny, maxx, maxy = self.bounds
            return minx <= point.x <= maxx and miny <= point.y <= maxy

        @property
        def wkt(self):
            joined = ", ".join(f"{x} {y}" for x, y in self.coords)
            return f"POLYGON (({joined}))"

    def box(minx, miny, maxx, maxy):
        return Polygon(
            [
                (minx, miny),
                (maxx, miny),
                (maxx, maxy),
                (minx, maxy),
                (minx, miny),
            ]
        )

    def shape(data):
        payload = data.get("geometry", data)
        if payload["type"] == "Point":
            x, y = payload["coordinates"]
            return Point(x, y)
        if payload["type"] == "Polygon":
            return Polygon(payload["coordinates"][0])
        raise ValueError("Unsupported geometry")

    def mapping(geom):
        if isinstance(geom, Point):
            return {"type": "Point", "coordinates": [geom.x, geom.y]}
        return {"type": "Polygon", "coordinates": [geom.coords]}

    def loads(wkt_text):
        wkt_text = wkt_text.strip()
        if wkt_text.upper().startswith("POINT"):
            inner = wkt_text[wkt_text.index("(") + 1 : wkt_text.index(")")].strip()
            x_str, y_str = inner.split()
            return Point(float(x_str), float(y_str))
        if wkt_text.upper().startswith("POLYGON"):
            inner = wkt_text.split("((", 1)[1].rsplit("))", 1)[0]
            coords = []
            for part in inner.split(","):
                x_str, y_str = part.strip().split()[:2]
                coords.append((float(x_str), float(y_str)))
            return Polygon(coords)
        raise ValueError("Unsupported WKT")

    shapely_module.LinearRing = LinearRing
    shapely_module.Point = Point
    shapely_module.Polygon = Polygon
    shapely_module.wkt = types.SimpleNamespace(loads=loads)
    shapely_geometry_module.Point = Point
    shapely_geometry_module.Polygon = Polygon
    shapely_geometry_module.shape = shape
    shapely_geometry_module.mapping = mapping
    shapely_geometry_module.box = box
    shapely_geometry_base_module.BaseGeometry = BaseGeometry
    shapely_ops_module.unary_union = lambda geometries: (
        geometries[0] if geometries else None
    )

    sys.modules["shapely"] = shapely_module
    sys.modules["shapely.geometry"] = shapely_geometry_module
    sys.modules["shapely.geometry.base"] = shapely_geometry_base_module
    sys.modules["shapely.ops"] = shapely_ops_module


def _install_geopandas_stub() -> None:
    if importlib.util.find_spec("geopandas") is not None:
        return

    geopandas_module = types.ModuleType("geopandas")

    class GeoSeries(list):
        def __init__(self, values, crs=None):
            super().__init__(values)
            self.crs = crs

        def estimate_utm_crs(self):
            return "EPSG:3857"

        def to_crs(self, _crs):
            return self

        @property
        def area(self):
            return [getattr(item, "area", 1.0) for item in self]

        @property
        def unary_union(self):
            return self[0] if self else None

        @property
        def __geo_interface__(self):
            features = []
            for item in self:
                coords = getattr(item, "coords", [])
                features.append(
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {"type": "Polygon", "coordinates": [coords]},
                    }
                )
            return {"type": "FeatureCollection", "features": features}

    class GeoDataFrame(list):
        def __init__(self, rows=None, columns=None, geometry=None, crs=None):
            super().__init__(rows or [])
            self.columns = columns or []
            self.crs = crs
            self.geometry = geometry

        @property
        def empty(self):
            return len(self) == 0

        def to_file(self, path):
            Path(path).write_text("{}", encoding="utf-8")

        def copy(self):
            return GeoDataFrame(
                list(self),
                columns=list(self.columns),
                geometry=self.geometry,
                crs=self.crs,
            )

    geopandas_module.GeoSeries = GeoSeries
    geopandas_module.GeoDataFrame = GeoDataFrame
    geopandas_module.read_file = lambda *_args, **_kwargs: GeoDataFrame()
    sys.modules["geopandas"] = geopandas_module


def _install_pandas_stub() -> None:
    if importlib.util.find_spec("pandas") is not None:
        return

    pandas_module = types.ModuleType("pandas")
    pandas_module.to_datetime = lambda value, **_kwargs: value
    pandas_module.concat = lambda frames: frames[0] if len(frames) == 1 else frames
    pandas_module.notna = lambda value: value is not None
    sys.modules["pandas"] = pandas_module


def _install_tabulate_stub() -> None:
    if importlib.util.find_spec("tabulate") is not None:
        return

    tabulate_module = types.ModuleType("tabulate")
    tabulate_module.tabulate = lambda rows, headers=None, tablefmt=None: json.dumps(
        {"rows": rows, "headers": headers, "tablefmt": tablefmt}
    )
    sys.modules["tabulate"] = tabulate_module


def _install_timezonefinder_stub() -> None:
    if importlib.util.find_spec("timezonefinder") is not None:
        return

    timezonefinder_module = types.ModuleType("timezonefinder")

    class TimezoneFinder:
        def timezone_at(self, **_kwargs):
            return "UTC"

        def closest_timezone_at(self, **_kwargs):
            return "UTC"

    timezonefinder_module.TimezoneFinder = TimezoneFinder
    sys.modules["timezonefinder"] = timezonefinder_module


def _install_rasterio_stub() -> None:
    if importlib.util.find_spec("rasterio") is not None:
        return

    rasterio_module = types.ModuleType("rasterio")

    def open_(*_args, **_kwargs):
        raise RuntimeError("rasterio.open stub called unexpectedly")

    rasterio_module.open = open_
    sys.modules["rasterio"] = rasterio_module


def _install_leafmap_stub() -> None:
    if importlib.util.find_spec("leafmap") is not None:
        return

    leafmap_module = types.ModuleType("leafmap")
    leafmap_module.nasa_data_search = lambda **_kwargs: (_kwargs, None)
    sys.modules["leafmap"] = leafmap_module


def _install_openpyxl_stub() -> None:
    if importlib.util.find_spec("openpyxl") is not None:
        return

    openpyxl_module = types.ModuleType("openpyxl")
    openpyxl_styles_module = types.ModuleType("openpyxl.styles")

    class Font:
        def __init__(self, bold=False):
            self.bold = bold

    class _Cell:
        def __init__(self, value=None, column_letter="A"):
            self.value = value
            self.font = None
            self.column_letter = column_letter

    class _ColumnDimension:
        def __init__(self):
            self.width = None

    class _ColumnDimensions(dict):
        def __missing__(self, key):
            value = _ColumnDimension()
            self[key] = value
            return value

    class Worksheet:
        def __init__(self):
            self.title = "Sheet"
            self.rows = []
            self.freeze_panes = None
            self.column_dimensions = _ColumnDimensions()

        def append(self, row):
            self.rows.append(list(row))

        def __getitem__(self, key):
            if key != 1:
                raise KeyError(key)
            row = self.rows[0] if self.rows else []
            return [
                _Cell(value=value, column_letter=chr(65 + index))
                for index, value in enumerate(row)
            ]

        @property
        def columns(self):
            if not self.rows:
                return []
            width = max(len(row) for row in self.rows)
            columns = []
            for index in range(width):
                letter = chr(65 + index)
                column = []
                for row in self.rows:
                    value = row[index] if index < len(row) else None
                    column.append(_Cell(value=value, column_letter=letter))
                columns.append(column)
            return columns

    class Workbook:
        def __init__(self):
            self.active = Worksheet()

        def save(self, path):
            Path(path).write_text(json.dumps(self.active.rows), encoding="utf-8")

    openpyxl_module.Workbook = Workbook
    openpyxl_styles_module.Font = Font
    sys.modules["openpyxl"] = openpyxl_module
    sys.modules["openpyxl.styles"] = openpyxl_styles_module


def _install_matplotlib_stub() -> None:
    if importlib.util.find_spec("matplotlib") is not None:
        return

    matplotlib_module = types.ModuleType("matplotlib")
    matplotlib_pyplot_module = types.ModuleType("matplotlib.pyplot")
    matplotlib_colors_module = types.ModuleType("matplotlib.colors")

    def get_cmap(_name):
        return lambda index: f"#{index:06x}"[-7:]

    matplotlib_pyplot_module.get_cmap = get_cmap
    matplotlib_colors_module.to_hex = lambda value: (
        value if isinstance(value, str) else "#000000"
    )

    sys.modules["matplotlib"] = matplotlib_module
    sys.modules["matplotlib.pyplot"] = matplotlib_pyplot_module
    sys.modules["matplotlib.colors"] = matplotlib_colors_module


def _install_folium_stub() -> None:
    if importlib.util.find_spec("folium") is not None:
        return

    folium_module = types.ModuleType("folium")

    class _Base:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.children = []

        def add_to(self, parent):
            parent.children.append(self)
            return self

    class Map(_Base):
        def get_root(self):
            return self

        def add_child(self, child):
            self.children.append(child)
            return child

        def save(self, path):
            Path(path).write_text("<html></html>", encoding="utf-8")

    class TileLayer(_Base):
        pass

    class FeatureGroup(_Base):
        pass

    class GeoJson(_Base):
        pass

    class Popup(_Base):
        pass

    class Icon(_Base):
        pass

    class Marker(_Base):
        pass

    class LayerControl(_Base):
        pass

    folium_module.Map = Map
    folium_module.TileLayer = TileLayer
    folium_module.FeatureGroup = FeatureGroup
    folium_module.GeoJson = GeoJson
    folium_module.Popup = Popup
    folium_module.Icon = Icon
    folium_module.Marker = Marker
    folium_module.LayerControl = LayerControl
    sys.modules["folium"] = folium_module


def _install_branca_stub() -> None:
    if importlib.util.find_spec("branca") is not None:
        return

    branca_module = types.ModuleType("branca")
    branca_element_module = types.ModuleType("branca.element")

    class MacroElement:
        pass

    branca_element_module.MacroElement = MacroElement
    sys.modules["branca"] = branca_module
    sys.modules["branca.element"] = branca_element_module


def _install_jinja2_stub() -> None:
    if importlib.util.find_spec("jinja2") is not None:
        return

    jinja2_module = types.ModuleType("jinja2")

    class Template:
        def __init__(self, text):
            self.text = text

    jinja2_module.Template = Template
    sys.modules["jinja2"] = jinja2_module


_install_numpy_stub()
_install_shapely_stub()
_install_geopandas_stub()
_install_pandas_stub()
_install_tabulate_stub()
_install_timezonefinder_stub()
_install_rasterio_stub()
_install_leafmap_stub()
_install_openpyxl_stub()
_install_matplotlib_stub()
_install_folium_stub()
_install_branca_stub()
_install_jinja2_stub()
