import copy
import json
import types
from collections import defaultdict


class FakeSeries:
    def __init__(self, values):
        self.values = list(values)

    @property
    def dt(self):
        return FakeDateTimeAccessor(self.values)

    def notnull(self):
        return FakeSeries([value is not None for value in self.values])

    def notna(self):
        return self.notnull()

    def any(self):
        return any(self.values)

    def astype(self, _kind):
        return FakeSeries([str(value) for value in self.values])

    def max(self):
        return max(self.values) if self.values else None

    def tolist(self):
        return list(self.values)

    def unique(self):
        seen = []
        for value in self.values:
            if value not in seen:
                seen.append(value)
        return seen

    def apply(self, func):
        return FakeSeries([func(value) for value in self.values])

    def isin(self, candidates):
        return [value in candidates for value in self.values]

    def __iter__(self):
        return iter(self.values)

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        return self.values[index]

    def __ne__(self, other):
        return FakeSeries([value != other for value in self.values])

    def __eq__(self, other):
        if isinstance(other, list):
            return [left == right for left, right in zip(self.values, other)]
        return FakeSeries([value == other for value in self.values])

    def __ge__(self, other):
        return [value >= other for value in self.values]

    def __le__(self, other):
        return [value <= other for value in self.values]


class FakeDateTimeAccessor:
    def __init__(self, values):
        self.values = values

    @property
    def date(self):
        return FakeSeries([value.date() for value in self.values])

    def strftime(self, fmt):
        return FakeSeries([value.strftime(fmt) for value in self.values])


class FakeColumns(list):
    def get_loc(self, key):
        return self.index(key)


class FakeRow:
    def __init__(self, mapping):
        self._mapping = mapping

    def __getattr__(self, name):
        try:
            return self._mapping[name]
        except KeyError as error:
            raise AttributeError(name) from error

    def __getitem__(self, key):
        return self._mapping[key]

    def __setitem__(self, key, value):
        self._mapping[key] = value

    def get(self, key, default=None):
        return self._mapping.get(key, default)


class _ILocAccessor:
    def __init__(self, frame):
        self.frame = frame

    def __getitem__(self, index):
        if isinstance(index, tuple):
            row_index, column_index = index
            column_name = self.frame.columns[column_index]
            return self.frame.rows[row_index][column_name]
        return FakeRow(self.frame.rows[index])

    def __setitem__(self, index, value):
        row_index, column_index = index
        column_name = self.frame.columns[column_index]
        self.frame.rows[row_index][column_name] = value


class _AtAccessor:
    def __init__(self, frame):
        self.frame = frame

    def __setitem__(self, index, value):
        row_index, column_name = index
        if column_name not in self.frame.columns:
            self.frame.columns.append(column_name)
        self.frame.rows[row_index][column_name] = value


class _LocAccessor:
    def __init__(self, frame):
        self.frame = frame

    def __getitem__(self, mask):
        return FakeFrame([row for row, keep in zip(self.frame.rows, mask) if keep])


class FakeGroupBy:
    def __init__(self, frame, group_cols):
        self.frame = frame
        self.group_cols = group_cols

    def agg(self, mapping):
        grouped = defaultdict(list)
        for row in self.frame.rows:
            key = tuple(row.get(col) for col in self.group_cols)
            grouped[key].append(row)

        rows = []
        for key, values in grouped.items():
            new_row = {col: value for col, value in zip(self.group_cols, key)}
            for column, aggregator in mapping.items():
                column_values = [value.get(column) for value in values]
                if aggregator == "first":
                    new_row[column] = column_values[0]
                elif aggregator == "max":
                    new_row[column] = max(column_values)
                elif aggregator == "mean":
                    new_row[column] = sum(column_values) / len(column_values)
                elif callable(aggregator):
                    new_row[column] = aggregator(column_values)
                else:
                    new_row[column] = column_values
            rows.append(new_row)
        return FakeFrame(rows)

    def __getitem__(self, column_name):
        return FakeTransform(self.frame, self.group_cols, column_name)


class FakeTransform:
    def __init__(self, frame, group_cols, column_name):
        self.frame = frame
        self.group_cols = group_cols
        self.column_name = column_name

    def transform(self, operation):
        if operation != "max":
            raise ValueError(operation)
        maxima = {}
        for row in self.frame.rows:
            key = tuple(row.get(col) for col in self.group_cols)
            maxima[key] = max(maxima.get(key, row[self.column_name]), row[self.column_name])
        return [maxima[tuple(row.get(col) for col in self.group_cols)] for row in self.frame.rows]


class FakeFrame:
    def __init__(self, rows):
        self.rows = [dict(row) for row in rows]
        keys = []
        for row in self.rows:
            for key in row:
                if key not in keys:
                    keys.append(key)
        self.columns = FakeColumns(keys)
        self.iloc = _ILocAccessor(self)
        self.at = _AtAccessor(self)
        self.loc = _LocAccessor(self)
        self.crs = "EPSG:4326"

    @property
    def empty(self):
        return len(self.rows) == 0

    def __len__(self):
        return len(self.rows)

    @property
    def index(self):
        return list(range(len(self.rows)))

    @property
    def geometry(self):
        return FakeSeries([row.get("geometry") for row in self.rows])

    @property
    def __geo_interface__(self):
        features = []
        for row in self.rows:
            geom = row.get("geometry")
            coords = getattr(geom, "coords", [])
            features.append(
                {
                    "type": "Feature",
                    "properties": {key: value for key, value in row.items() if key != "geometry"},
                    "geometry": {"type": "Polygon", "coordinates": [coords]},
                }
            )
        return {"type": "FeatureCollection", "features": features}

    def copy(self):
        return FakeFrame(copy.deepcopy(self.rows))

    def iterrows(self):
        for index, row in enumerate(self.rows):
            yield index, FakeRow(row)

    def sort_values(self, column, ascending=True):
        return FakeFrame(
            sorted(
                self.rows,
                key=lambda row: row[column],
                reverse=not ascending,
            )
        )

    def reset_index(self, drop=False):
        return FakeFrame(self.rows)

    def drop_duplicates(self, subset=None):
        seen = set()
        unique = []
        subset = subset or self.columns
        for row in self.rows:
            key = tuple(row.get(name) for name in subset)
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        return FakeFrame(unique)

    def groupby(self, group_cols, dropna=False, sort=False):
        return FakeGroupBy(self, group_cols)

    def apply(self, func, axis=0):
        if axis != 1:
            raise ValueError(axis)
        results = [func(FakeRow(row)) for row in self.rows]
        # When func returns FakeRow (row-level transformation), return a FakeFrame
        # so callers can chain .dropna()/.reset_index(). Scalar returns stay as a list.
        if any(isinstance(r, FakeRow) for r in results):
            kept = [r._mapping for r in results if isinstance(r, FakeRow)]
            return FakeFrame(kept)
        return results

    def drop(self, columns=None):
        columns = columns or []
        return FakeFrame(
            [
                {key: value for key, value in row.items() if key not in columns}
                for row in self.rows
            ]
        )

    def dropna(self, subset=None):
        subset = subset or []
        return FakeFrame(
            [
                row
                for row in self.rows
                if all(row.get(column) is not None for column in subset)
            ]
        )

    def to_json(self):
        return json.dumps(self.__geo_interface__)

    def to_file(self, path):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.__geo_interface__, handle)

    def __getitem__(self, key):
        if isinstance(key, str):
            return FakeSeries([row.get(key) for row in self.rows])
        if isinstance(key, list):
            if all(isinstance(item, bool) for item in key):
                return FakeFrame([row for row, keep in zip(self.rows, key) if keep])
            return FakeFrame([{column: row.get(column) for column in key} for row in self.rows])
        raise TypeError(key)

    def __setitem__(self, key, value):
        if key not in self.columns:
            self.columns.append(key)
        if isinstance(value, FakeSeries):
            value = value.values
        if isinstance(value, list):
            for row, row_value in zip(self.rows, value):
                row[key] = row_value
        else:
            for row in self.rows:
                row[key] = value


class FakeGeometry:
    def __init__(
        self,
        name="geom",
        area=10.0,
        geom_type="Polygon",
        centroid_x=0.0,
        centroid_y=0.0,
        wkt=None,
        empty=False,
        valid=True,
    ):
        self.name = name
        self.area = area
        self.geom_type = geom_type
        self.x = centroid_x
        self.y = centroid_y
        self.centroid = types.SimpleNamespace(x=centroid_x, y=centroid_y)
        self.wkt = wkt or name
        self._empty = empty
        self._valid = valid
        self.coords = [(0, 0), (1, 0), (1, 1), (0, 0)]

    @property
    def is_empty(self):
        return self._empty

    @property
    def is_valid(self):
        return self._valid

    @property
    def bounds(self):
        return (0.0, 0.0, 1.0, 1.0)

    @property
    def __geo_interface__(self):
        if self.geom_type == "Point":
            return {"type": "Point", "coordinates": (self.x, self.y)}
        return {"type": "Polygon", "coordinates": [self.coords]}

    def buffer(self, _distance):
        return FakeGeometry(
            name=f"{self.name}-buffered",
            area=self.area,
            geom_type=self.geom_type,
            centroid_x=self.centroid.x,
            centroid_y=self.centroid.y,
            wkt=self.wkt,
            empty=self._empty,
            valid=True,
        )

    def intersection(self, other):
        area = min(self.area, getattr(other, "area", self.area))
        return FakeGeometry(
            name=f"{self.name}&{getattr(other, 'name', 'other')}",
            area=area,
            geom_type=self.geom_type,
            centroid_x=self.centroid.x,
            centroid_y=self.centroid.y,
        )

    def intersects(self, _other):
        return not self._empty


class FakePoint(FakeGeometry):
    def __init__(self, x=0.0, y=0.0):
        super().__init__(name="point", area=0.0, geom_type="Point", centroid_x=x, centroid_y=y)
        self.x = x
        self.y = y
        self.centroid = self


class FakePolygon(FakeGeometry):
    def __init__(self, name="polygon", area=10.0, centroid_x=0.0, centroid_y=0.0, empty=False, valid=True):
        super().__init__(
            name=name,
            area=area,
            geom_type="Polygon",
            centroid_x=centroid_x,
            centroid_y=centroid_y,
            empty=empty,
            valid=valid,
        )
