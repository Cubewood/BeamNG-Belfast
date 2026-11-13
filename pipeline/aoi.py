"""Map squares and coordinate transforms (T-201): lat/lon <-> projected (Irish Grid, ITM) <-> game world.

    import aoi
    cfg = aoi.load()                       # reads config/aoi.yaml
    s = cfg.area("slice")
    x, y = s.latlon_to_game(54.5428, -5.93)
    lat, lon = s.game_to_latlon(x, y)
    e, n = s.game_to_crs("itm", x, y)      # ITM easting, northing
    x, y = s.crs_to_game("irish_grid", 334013.47, 368022.08)

All functions take floats or numpy arrays and return the same kind. `latlon` order is (lat, lon);
projected coordinates are (easting, northing); game world is (x east, y north), metres, origin at the
middle of the square. The game-world frame is MapNG's: linear in latitude and longitude across the
square's bounds (config/aoi.yaml explains; docs/aoi.md has the checks). Points outside the square are
extrapolated, not clamped as MapNG's own geoToWorldPoint does.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import numpy as np
import yaml
from pyproj import CRS, Transformer

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "aoi.yaml"


@lru_cache(maxsize=None)
def _transformer(src_proj4: str, dst_proj4: str) -> Transformer:
    return Transformer.from_crs(CRS.from_proj4(src_proj4), CRS.from_proj4(dst_proj4), always_xy=True)


def _like(a, b):
    """Return b as a float if a was a plain number, else as an array."""
    return float(b) if np.ndim(a) == 0 else np.asarray(b)


@dataclass(frozen=True)
class Bounds:
    north: float
    south: float
    west: float
    east: float

    def as_dict(self) -> dict:
        return {"north": self.north, "south": self.south, "west": self.west, "east": self.east}


def local_bounds(lat: float, lon: float, half_m: float, tmerc_template: str) -> Bounds:
    """The bounds MapNG's fetch makes: corners at +-half_m in a local transverse Mercator at (lat, lon)."""
    tm = tmerc_template.format(lat=repr(lat), lon=repr(lon))
    to_wgs = Transformer.from_crs(CRS.from_proj4(tm), CRS.from_proj4("+proj=longlat +datum=WGS84 +no_defs"),
                                  always_xy=True)
    corners = [to_wgs.transform(cx, cy) for cx, cy in ((-half_m, half_m), (half_m, half_m), (-half_m, -half_m),
                                                       (half_m, -half_m))]
    lons = [c[0] for c in corners]
    lats = [c[1] for c in corners]
    return Bounds(north=max(lats), south=min(lats), west=min(lons), east=max(lons))


@dataclass
class Config:
    raw: dict
    crs: dict = field(init=False)
    points: dict = field(init=False)
    _areas: dict = field(init=False, default_factory=dict)

    def __post_init__(self):
        self.crs = self.raw["crs"]
        self.points = self.raw.get("points", {})
        for name, a in self.raw["areas"].items():
            self._areas[name] = Area(name, a, self)

    @property
    def areas(self) -> dict:
        return self._areas

    def area(self, name: str) -> "Area":
        return self._areas[name]

    def proj4(self, crs_name: str) -> str:
        return self.crs[crs_name]["proj4"]

    # Plain CRS-to-CRS conversion, no area involved.
    def latlon_to_crs(self, crs_name: str, lat, lon):
        x, y = _transformer(self.proj4("wgs84"), self.proj4(crs_name)).transform(np.asarray(lon, float),
                                                                                  np.asarray(lat, float))
        return _like(lat, x), _like(lat, y)

    def crs_to_latlon(self, crs_name: str, easting, northing):
        lon, lat = _transformer(self.proj4(crs_name), self.proj4("wgs84")).transform(
            np.asarray(easting, float), np.asarray(northing, float))
        return _like(easting, lat), _like(easting, lon)

    def convert(self, src: str, dst: str, a, b):
        """Between two named CRSs: (easting|lon, northing|lat) in and out, in each system's own order."""
        x, y = _transformer(self.proj4(src), self.proj4(dst)).transform(np.asarray(a, float), np.asarray(b, float))
        return _like(a, x), _like(a, y)


class Area:
    """One map square with its game-world frame."""

    def __init__(self, name: str, spec: dict, cfg: Config):
        self.name = name
        self.cfg = cfg
        self.spec = spec
        self.lat0 = float(spec["centre"]["lat"])
        self.lon0 = float(spec["centre"]["lon"])
        self.size_m = int(spec["size_m"])
        self.mpp = float(spec.get("mpp", 1))
        gw = cfg.raw["game_world"]
        if gw.get("method") != "mapng_linear_bounds":
            raise ValueError(f"unknown game_world method {gw.get('method')!r}")
        self.margin_m = float(gw["margin_m"])
        if math.log2(self.size_m) % 1:
            raise ValueError(f"{name}: size_m {self.size_m} is not a power of two (MapNG needs one)")
        if "bounds" in spec:
            self.bounds = Bounds(**{k: float(spec["bounds"][k]) for k in ("north", "south", "west", "east")})
            self.bounds_frozen = True
        else:
            self.bounds = local_bounds(self.lat0, self.lon0, self.size_m * self.mpp / 2 + self.margin_m,
                                       gw["tmerc"])
            self.bounds_frozen = False
        self._computed = None

    # ---- bounds ----
    def computed_bounds(self) -> Bounds:
        """Bounds recomputed from the centre, size and margin (equal to `bounds` unless those were frozen)."""
        if self._computed is None:
            gw = self.cfg.raw["game_world"]
            self._computed = local_bounds(self.lat0, self.lon0, self.size_m * self.mpp / 2 + self.margin_m,
                                          gw["tmerc"])
        return self._computed

    @property
    def size_px(self) -> int:
        return int(round(self.size_m / self.mpp))

    @property
    def world_size(self) -> float:
        """Game-world side length in game metres (pixels times MapNG's square size)."""
        return float(self.size_px * self.mpp)

    # ---- lat/lon <-> game world ----
    def latlon_to_game(self, lat, lon):
        b = self.bounds
        lat = np.asarray(lat, float)
        lon = np.asarray(lon, float)
        ws = self.world_size
        x = ((lon - b.west) / (b.east - b.west) - 0.5) * ws
        y = (0.5 - (b.north - lat) / (b.north - b.south)) * ws
        return _like(lat, x), _like(lat, y)

    def game_to_latlon(self, x, y):
        b = self.bounds
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        ws = self.world_size
        lon = b.west + (x / ws + 0.5) * (b.east - b.west)
        lat = b.north - (0.5 - y / ws) * (b.north - b.south)
        return _like(x, lat), _like(x, lon)

    # ---- projected CRS <-> game world (through lat/lon) ----
    def game_to_crs(self, crs_name: str, x, y):
        lat, lon = self.game_to_latlon(x, y)
        return self.cfg.latlon_to_crs(crs_name, lat, lon)

    def crs_to_game(self, crs_name: str, easting, northing):
        lat, lon = self.cfg.crs_to_latlon(crs_name, easting, northing)
        return self.latlon_to_game(lat, lon)

    # ---- square geometry ----
    def contains_latlon(self, lat, lon, margin_m: float = 0.0):
        """True where the point is inside the game-world square, `margin_m` game metres in from the edge."""
        x, y = self.latlon_to_game(lat, lon)
        h = self.world_size / 2 - margin_m
        return (np.abs(x) <= h) & (np.abs(y) <= h)

    def ground_scale(self) -> tuple[float, float]:
        """Real ground metres per game metre, (east-west, north-south), at the centre of the square.

        Measured in the local transverse Mercator at the centre. About 1.004 on the slice, because MapNG's
        bounds include `margin_m` on every side."""
        gw = self.cfg.raw["game_world"]
        tm = gw["tmerc"].format(lat=repr(self.lat0), lon=repr(self.lon0))
        t = Transformer.from_crs(CRS.from_proj4("+proj=longlat +datum=WGS84 +no_defs"), CRS.from_proj4(tm),
                                 always_xy=True)
        lat_c, lon_c = self.game_to_latlon(0.0, 0.0)
        x0, y0 = t.transform(lon_c, lat_c)
        la1, lo1 = self.game_to_latlon(100.0, 100.0)
        x1, y1 = t.transform(lo1, la1)
        return ((x1 - x0) / 100.0, (y1 - y0) / 100.0)

    # ---- heightmap grid ----
    def pixel_to_game(self, col, row):
        """Where the game terrain puts heightmap sample (col, row); row 0 is the north edge."""
        col = np.asarray(col, float)
        row = np.asarray(row, float)
        n = self.size_px
        k = self.mpp
        return _like(col, (col - n / 2) * k), _like(col, (n / 2 - 1 - row) * k)

    def game_to_pixel(self, x, y):
        x = np.asarray(x, float)
        y = np.asarray(y, float)
        n = self.size_px
        k = self.mpp
        return _like(x, x / k + n / 2), _like(x, n / 2 - 1 - y / k)

    def pixel_to_latlon_mapng(self, col, row):
        """Where MapNG sampled its DEM for heightmap pixel (col, row): spacing span / (size_px - 1).

        This is NOT where the game terrain draws that sample (see pixel_to_game); the two differ by up to
        one game metre (north and east edges) and agree at the south-west corner."""
        b = self.bounds
        col = np.asarray(col, float)
        row = np.asarray(row, float)
        n = self.size_px
        lat = b.north - row / (n - 1) * (b.north - b.south)
        lon = b.west + col / (n - 1) * (b.east - b.west)
        return _like(col, lat), _like(col, lon)

    def terrain_shift(self, col, row):
        """Game metres (dx, dy) from MapNG's DEM sample position to where the terrain draws the sample."""
        lat, lon = self.pixel_to_latlon_mapng(col, row)
        sx, sy = self.latlon_to_game(lat, lon)
        tx, ty = self.pixel_to_game(col, row)
        return tx - sx, ty - sy


def load(path: str | Path | None = None) -> Config:
    p = Path(path) if path else CONFIG_PATH
    with open(p, encoding="utf-8") as f:
        return Config(yaml.safe_load(f))


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres on a 6371008.8 m sphere; independent of every transform above."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dp = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    h = np.sin(dp / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * 6371008.8 * np.arcsin(np.sqrt(h))
