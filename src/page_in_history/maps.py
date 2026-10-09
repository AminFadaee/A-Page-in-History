import hashlib
import io
import itertools
import math
import pathlib
import textwrap
from dataclasses import dataclass
from enum import StrEnum

import geopandas as gpd
import matplotlib
import numpy as np

matplotlib.use("Agg")
matplotlib.rcParams["hatch.linewidth"] = 2.0
import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
from matplotlib import font_manager, patheffects
from PIL import Image
from pyproj import Transformer
from shapely import MultiLineString, MultiPolygon, Polygon, make_valid
from shapely.geometry import Point, box
from shapely.ops import polylabel

from page_in_history.naming import year_label
from page_in_history.sources.cliopatria import Cliopatria

FIGURE_SIZE = (10, 5.625)
DPI = 160
PADDING = 0.35
MIN_SPAN_METERS = 1_800_000
CLIP_LONGITUDE = 170
CLIP_LATITUDE = 89
PALETTE_COLORS = 128
WORLD_PROJECTION = "ESRI:54030"

LINE_WIDTH = 0.5
SMOOTHING_PIXELS = 1.0
SMOOTHING_PASSES = 2
TOUCH_DISTANCE = 20_000

RIVER_WIDTHS = {1: 1.5, 2: 1.35, 3: 1.2, 4: 1.05, 5: 0.9}
OVERLAP_HATCH = "///"
MIN_OVERLAP_PIXELS = 4
LOCAL_RIVER_RANK = 5
CAPITAL_SIZE = 110
CAPITAL_MERGE_PIXELS = 10

INSET_WIDTH = 0.2
INSET_MARGIN = 0.015
CAPTION_SIZE = 12

NEIGHBOUR_SIZES = (11, 8.5)
OTHER_SIZES = (8.5, 7)
NEIGHBOUR_INSIDE = 0.55
OTHER_INSIDE = 0.7
NEIGHBOUR_ON_FOCUS = 0.12
OTHER_ON_FOCUS = 0.02
MIN_VISIBLE_SHARE = 0.5
MIN_FRAME_SHARE = 0.0015
LABEL_PARTS = 3
LETTER_SPACE = " "
LABEL_SHADE = 0.42


class Color(StrEnum):
    OCEAN = "#AEDFF7"
    RIVER = "#5fa8d8"
    LAND = "#e9dfc7"
    LINE = "#5f5f5f"
    LINE_ON_FOCUS = "#f3d9dd"
    HIGHLIGHT = "#A6192E"
    CAPITAL = "#ffffff"
    TEXT = "#2b2b2b"
    HALO = "#ffffff"
    INSET_LAND = "#c5c5c5"
    INSET_BACKGROUND = "#ffffff"
    INSET_FRAME = "#A6192E"


NEIGHBOUR_COLORS = ("#b5cc96", "#93c2b5", "#9db0d6", "#bca6d6", "#dba6c1", "#e2b088", "#d2c47c", "#c98f86")


@dataclass(frozen=True)
class Corner:
    left: bool
    bottom: bool


CORNERS = (Corner(True, True), Corner(False, True), Corner(True, False), Corner(False, False))


def chaikin(coordinates: np.ndarray, passes: int) -> np.ndarray:
    """Corner cutting: each pass replaces every segment by points a quarter and three quarters along it."""
    for _ in range(passes):
        start, end = coordinates[:-1], coordinates[1:]
        cut = np.empty((len(start) * 2, 2))
        cut[0::2] = 0.75 * start + 0.25 * end
        cut[1::2] = 0.25 * start + 0.75 * end
        coordinates = np.vstack([cut, cut[:1]])
    return coordinates


def smooth(geometry, tolerance: float):
    """Softens hand-traced stair steps by about a pixel without visibly moving borders."""
    def ring(coordinates) -> np.ndarray:
        points = np.asarray(coordinates)[:, :2]
        return chaikin(points, SMOOTHING_PASSES) if len(points) >= 4 else points

    def polygon(part: Polygon):
        part = part.simplify(tolerance)
        if part.is_empty or part.geom_type != "Polygon":
            return part
        return Polygon(ring(part.exterior.coords), [ring(interior.coords) for interior in part.interiors])

    parts = [polygon(part) for part in getattr(geometry, "geoms", [geometry]) if part.geom_type == "Polygon"]
    return make_valid(MultiPolygon([part for part in parts if part.geom_type == "Polygon" and not part.is_empty]))


def neighbour_colors(polities: gpd.GeoDataFrame) -> dict[str, str]:
    """Touching polities never share a colour; each start is seeded by name so maps stay stable between runs."""
    assigned: dict[str, str] = {}
    by_size = polities.assign(size=polities.geometry.area).sort_values("size", ascending=False)
    index = by_size.sindex
    for row in by_size.itertuples():
        nearby = by_size.iloc[index.query(row.geometry.buffer(TOUCH_DISTANCE), predicate="intersects")]
        taken = {assigned[name] for name in nearby.Name if name in assigned}
        seed = int(hashlib.sha256(row.Name.encode()).hexdigest(), 16) % len(NEIGHBOUR_COLORS)
        ordered = NEIGHBOUR_COLORS[seed:] + NEIGHBOUR_COLORS[:seed]
        assigned[row.Name] = next((color for color in ordered if color not in taken), ordered[0])
    return assigned


def shade(color: str, amount: float) -> tuple[float, float, float]:
    red, green, blue = mcolors.to_rgb(color)
    return red * amount, green * amount, blue * amount


def layouts(name: str) -> list[str]:
    """One line, then two balanced lines, then a word per line, in spaced capitals."""
    words = name.upper().split()
    width = max(len(" ".join(words)) // 2 + 1, max(len(word) for word in words))
    options = [[" ".join(words)], textwrap.wrap(" ".join(words), width, break_long_words=False), words]
    return list(dict.fromkeys("\n".join(LETTER_SPACE.join(line) for line in lines) for lines in options))


def label_point(geometry) -> Point:
    return polylabel(geometry, tolerance=1_000) if geometry.geom_type == "Polygon" else geometry.representative_point()


def frame_around(bounds):
    """The civilization's bounds with padding, widened to the figure's aspect ratio."""
    left, bottom, right, top = bounds
    aspect = FIGURE_SIZE[0] / FIGURE_SIZE[1]
    half_width = max(right - left, MIN_SPAN_METERS) * (0.5 + PADDING)
    half_height = (top - bottom) * (0.5 + PADDING)
    half_width = max(half_width, half_height * aspect)
    half_height = half_width / aspect
    center_x, center_y = (left + right) / 2, (bottom + top) / 2
    return box(center_x - half_width, center_y - half_height, center_x + half_width, center_y + half_height)


class Scene:
    """One map's layers, projected around the civilization, smoothed and cut to the coastline."""

    def __init__(self, renderer: "MapRenderer", name: str, year: int):
        polities = renderer.cliopatria.at(year)
        center = polities[polities.Name == name].geometry.union_all().representative_point()
        self.crs = f"+proj=laea +lat_0={center.y:.4f} +lon_0={center.x:.4f} +datum=WGS84 +units=m"
        self.clip = box(center.x - CLIP_LONGITUDE, -CLIP_LATITUDE, center.x + CLIP_LONGITUDE, CLIP_LATITUDE)
        self.land = self.project(renderer.land).geometry.union_all()
        projected = self.project(polities)
        self.frame = frame_around(projected[projected.Name == name].total_bounds)
        pixel = (self.frame.bounds[2] - self.frame.bounds[0]) / (FIGURE_SIZE[0] * DPI)
        projected = projected.set_geometry(
            projected.geometry.apply(lambda shape: smooth(shape, SMOOTHING_PIXELS * pixel).intersection(self.land)))
        projected = projected[~projected.geometry.is_empty]
        self.name = name
        self.pixel = pixel
        self.shapes = {polity: group.geometry.union_all() for polity, group in projected.groupby("Name")}
        self.focus = projected[projected.Name == name].geometry.union_all()
        self.others = projected[projected.Name != name]
        borders = self.project(renderer.countries).boundary.union_all().union(projected.boundary.union_all())
        self.lines = borders.intersection(self.frame.buffer(pixel * 10))
        rivers = self.project(renderer.rivers)
        self.rivers = rivers[(rivers.scalerank < LOCAL_RIVER_RANK) | rivers.intersects(self.focus)]

    def project(self, frame: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        frame = frame.clip(self.clip).to_crs(self.crs)
        return frame.set_geometry(frame.geometry.make_valid())


class MapRenderer:
    def __init__(self, cliopatria: Cliopatria, countries: gpd.GeoDataFrame, rivers: gpd.GeoDataFrame,
                 font: pathlib.Path):
        self.cliopatria = cliopatria
        self.countries = countries
        self.rivers = rivers
        self.land = gpd.GeoDataFrame(geometry=[countries.geometry.union_all()], crs=countries.crs)
        self.world = self.land.to_crs(WORLD_PROJECTION)
        font_manager.fontManager.addfont(str(font))
        self.font = font_manager.FontProperties(fname=font).get_name()

    def render(self, name: str, year: int, capitals: list[tuple[float, float]], output: pathlib.Path) -> list[str]:
        """Draws the civilization at its year; returns the polities that got a label."""
        scene = Scene(self, name, year)
        figure, axes = plt.subplots(figsize=FIGURE_SIZE, dpi=DPI)
        figure.subplots_adjust(0, 0, 1, 1)
        figure.patch.set_facecolor(Color.OCEAN)
        axes.set_axis_off()
        colors = neighbour_colors(scene.others)
        gpd.GeoSeries([scene.land], crs=scene.crs).plot(ax=axes, color=Color.LAND, linewidth=0)
        scene.others.plot(ax=axes, color=[colors[other] for other in scene.others.Name], linewidth=0)
        gpd.GeoSeries([scene.focus], crs=scene.crs).plot(ax=axes, color=Color.HIGHLIGHT, linewidth=0)
        self._overlaps(axes, scene, colors)
        for rank, width in RIVER_WIDTHS.items():
            rivers = scene.rivers[scene.rivers.scalerank == rank]
            if not rivers.empty:
                rivers.plot(ax=axes, color=Color.RIVER, linewidth=width, zorder=2)
        inner = scene.focus.buffer(-1_000)
        for lines, color in ((scene.lines.difference(inner), Color.LINE),
                             (scene.lines.intersection(inner), Color.LINE_ON_FOCUS)):
            gpd.GeoSeries([lines], crs=scene.crs).plot(ax=axes, color=color, linewidth=LINE_WIDTH, zorder=3)
        self._capitals(axes, scene, capitals)
        left, bottom, right, top = scene.frame.bounds
        axes.set_xlim(left, right)
        axes.set_ylim(bottom, top)
        corners = sorted(CORNERS, key=lambda corner: crowding(scene, corner))
        reserved = [self._inset(figure, scene, corners[0]), self._caption(figure, axes, year, corners[1])]
        labelled = self._labels(figure, axes, scene, colors, reserved)
        save(figure, output)
        return labelled

    @staticmethod
    def _overlaps(axes, scene: Scene, colors: dict[str, str]) -> None:
        """Land claimed by two polities in the same year gets one's colour striped with the other's, so neither
        silently wins."""
        fills = {**colors, scene.name: Color.HIGHLIGHT}
        minimum = (MIN_OVERLAP_PIXELS * scene.pixel) ** 2
        for (first, first_shape), (second, second_shape) in itertools.combinations(scene.shapes.items(), 2):
            if not first_shape.intersects(second_shape):
                continue
            shared = first_shape.intersection(second_shape)
            if shared.area < minimum:
                continue
            base, stripes = (first, second) if first == scene.name or second != scene.name else (second, first)
            gpd.GeoSeries([shared], crs=scene.crs).plot(ax=axes, facecolor=fills[base], edgecolor=fills[stripes],
                                                        hatch=OVERLAP_HATCH, linewidth=0, zorder=1.5)

    @staticmethod
    def _capitals(axes, scene: Scene, capitals: list[tuple[float, float]]) -> None:
        if not capitals:
            return
        points = gpd.GeoSeries([Point(location) for location in capitals], crs="EPSG:4326").to_crs(scene.crs)
        stars = merged(list(zip(points.x, points.y)), CAPITAL_MERGE_PIXELS * scene.pixel)
        axes.scatter([x for x, _ in stars], [y for _, y in stars], marker="*", s=CAPITAL_SIZE, color=Color.CAPITAL,
                     edgecolors=Color.LINE, linewidths=0.6, zorder=5)

    def _inset(self, figure, scene: Scene, corner: Corner):
        """A small world map with the frame outlined, in the least crowded corner."""
        x0, y0, x1, y1 = corner_box(scene, corner).bounds
        left, bottom, right, top = scene.frame.bounds
        inset = figure.add_axes([(x0 - left) / (right - left), (y0 - bottom) / (top - bottom),
                                 (x1 - x0) / (right - left), (y1 - y0) / (top - bottom)])
        inset.set_facecolor(Color.INSET_BACKGROUND)
        inset.set_axis_on()
        inset.set_xticks([])
        inset.set_yticks([])
        for spine in inset.spines.values():
            spine.set_edgecolor(Color.LINE)
            spine.set_linewidth(0.6)
        self.world.plot(ax=inset, color=Color.INSET_LAND, linewidth=0)
        outline = gpd.GeoSeries([frame_outline(scene)], crs="EPSG:4326").to_crs(WORLD_PROJECTION)
        outline.plot(ax=inset, color=Color.INSET_FRAME, linewidth=1)
        world_left, world_bottom, world_right, world_top = self.world.total_bounds
        inset.set_xlim(world_left, world_right)
        inset.set_ylim(world_bottom, world_top)
        inset.set_aspect("equal", adjustable="datalim")
        inset.set_xlabel("")
        inset.set_ylabel("")
        return inset.get_window_extent(figure.canvas.get_renderer())

    def _caption(self, figure, axes, year: int, corner: Corner):
        """The year the map shows, so the snapshot is not mistaken for the whole period."""
        x = INSET_MARGIN if corner.left else 1 - INSET_MARGIN
        y = INSET_MARGIN * 1.8 if corner.bottom else 1 - INSET_MARGIN * 1.8
        text = axes.text(x, y, year_label(year), transform=axes.transAxes, fontsize=CAPTION_SIZE, family=self.font, weight="semibold",
                         color=Color.TEXT, ha="left" if corner.left else "right",
                         va="bottom" if corner.bottom else "top", zorder=6,
                         path_effects=[patheffects.withStroke(linewidth=3, foreground=Color.HALO)])
        return text.get_window_extent(figure.canvas.get_renderer())

    def _labels(self, figure, axes, scene: Scene, colors: dict[str, str], reserved: list) -> list[str]:
        """Neighbours first and larger; others only when mostly in view and the name fits inside them."""
        visible = scene.others.assign(geometry=scene.others.geometry.intersection(scene.frame))
        keep = ~visible.geometry.is_empty
        visible, whole = visible[keep], scene.others[keep]
        visible = visible.assign(
            touching=visible.geometry.buffer(TOUCH_DISTANCE).intersects(scene.focus),
            in_view=visible.geometry.area / whole.geometry.area,
            share=visible.geometry.area / scene.frame.area,
        )
        visible = visible[visible.touching | ((visible.in_view >= MIN_VISIBLE_SHARE)
                                              & (visible.share >= MIN_FRAME_SHARE))]
        visible = visible.assign(size=visible.geometry.area).sort_values(["touching", "size"], ascending=False)
        placed, labelled = list(reserved), []
        for row in visible.itertuples():
            if self._place(figure, axes, scene, row, colors[row.Name], placed):
                labelled.append(row.Name)
        return labelled

    def _place(self, figure, axes, scene: Scene, row, fill: str, placed: list) -> bool:
        renderer = figure.canvas.get_renderer()
        to_data = axes.transData.inverted()
        sizes, inside, on_focus = ((NEIGHBOUR_SIZES, NEIGHBOUR_INSIDE, NEIGHBOUR_ON_FOCUS) if row.touching
                                   else (OTHER_SIZES, OTHER_INSIDE, OTHER_ON_FOCUS))
        parts = sorted(getattr(row.geometry, "geoms", [row.geometry]),
                       key=lambda part: (part.buffer(TOUCH_DISTANCE).intersects(scene.focus), part.area), reverse=True)
        for size in sizes:
            for layout in layouts(row.Name):
                for part in parts[:LABEL_PARTS]:
                    point = label_point(part)
                    text = axes.text(point.x, point.y, layout, fontsize=size, family=self.font,
                                     weight="semibold", color=shade(fill, LABEL_SHADE), ha="center", va="center", linespacing=1.1,
                                     zorder=4, path_effects=[patheffects.withStroke(linewidth=2, foreground=fill)])
                    extent = text.get_window_extent(renderer)
                    (x0, y0), (x1, y1) = to_data.transform([(extent.x0, extent.y0), (extent.x1, extent.y1)])
                    area = box(x0, y0, x1, y1)
                    if (area.intersection(row.geometry).area / area.area >= inside
                            and area.intersection(scene.focus).area / area.area <= on_focus
                            and axes.bbox.contains(extent.x0, extent.y0) and axes.bbox.contains(extent.x1, extent.y1)
                            and not any(extent.overlaps(other) for other in placed)):
                        placed.append(extent)
                        return True
                    text.remove()
        return False


def merged(points: list[tuple[float, float]], distance: float) -> list[tuple[float, float]]:
    """Capitals closer than a star's width share one star at their midpoint, so stars never pile up."""
    groups: list[list[tuple[float, float]]] = []
    for point in points:
        group = next((group for group in groups if any(math.dist(point, other) < distance for other in group)), None)
        if group is None:
            groups.append([point])
        else:
            group.append(point)
    return [(sum(x for x, _ in group) / len(group), sum(y for _, y in group) / len(group)) for group in groups]


def frame_outline(scene: Scene) -> MultiLineString:
    """The map's frame in longitude and latitude, split where it crosses the 180th meridian so the world inset does
    not draw a line straight across the globe."""
    edge = scene.frame.exterior.segmentize(20_000)
    longitudes, latitudes = Transformer.from_crs(scene.crs, "EPSG:4326", always_xy=True).transform(*edge.xy)
    lines, current = [], [(longitudes[0], latitudes[0])]
    for point in zip(longitudes[1:], latitudes[1:]):
        if abs(point[0] - current[-1][0]) > 180:
            lines.append(current)
            current = []
        current.append(point)
    lines.append(current)
    return MultiLineString([line for line in lines if len(line) > 1])


def corner_box(scene: Scene, corner: Corner):
    left, bottom, right, top = scene.frame.bounds
    width = (right - left) * INSET_WIDTH
    height = width * 0.55
    margin = (right - left) * INSET_MARGIN
    x0 = left + margin if corner.left else right - margin - width
    y0 = bottom + margin if corner.bottom else top - margin - height
    return box(x0, y0, x0 + width, y0 + height)


def crowding(scene: Scene, corner: Corner) -> float:
    area = corner_box(scene, corner)
    return 10 * area.intersection(scene.focus).area + area.intersection(scene.others.union_all()).area


def save(figure, output: pathlib.Path) -> None:
    buffer = io.BytesIO()
    figure.savefig(buffer, format="png", dpi=DPI, facecolor=figure.get_facecolor())
    plt.close(figure)
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.open(buffer).convert("RGB").quantize(PALETTE_COLORS).save(output, optimize=True)
