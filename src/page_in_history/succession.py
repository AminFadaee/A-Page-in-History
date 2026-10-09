from collections.abc import Callable
from dataclasses import dataclass

import geopandas as gpd
import numpy as np
import shapely
from shapely.geometry import Point

WINDOW_YEARS = 30
MIN_HOLD_YEARS = 5
HOLDING_GAP_YEARS = 5
HEARTLAND_GRID = 60
PARTITION_SHARE = 0.15
PARTITION_HOLDERS = 3
CAPITAL_TOLERANCE_DEGREES = 0.1


@dataclass(frozen=True)
class Holding:
    qid: str
    start: int
    end: int

    @property
    def years(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class Anchor:
    point: Point
    place: str
    region: str


class RegionalSuccession:
    """Who ruled a civilization's home region right before and right after it. The home is the capital it held
    longest, or without a confirmed capital the spot it held for the most years; the region is the modern country
    around it. The neighbour is the next holder on Cliopatria's maps within 30 years, and Wikidata or the Wikipedia
    infobox must also say it came before or after. Holders that stayed under five years, like a wartime occupation,
    are passed over, and an empire partitioned among three or more states has no single successor."""

    def __init__(self, frame: gpd.GeoDataFrame, qids: dict[str, str], countries: gpd.GeoDataFrame,
                 stated: Callable[[str, str], bool], equal_area: Callable):
        self.frame = frame.assign(qid=frame.Name.map(qids)).dropna(subset=["qid"])
        self.countries = countries.to_crs(frame.crs)
        self.stated = stated
        self.equal_area = equal_area

    def neighbours(self, qid: str, capitals: list[dict]) -> tuple[dict, dict, str]:
        rows = self.frame[self.frame.qid == qid]
        if rows.empty:
            return {}, {}, ""
        anchor = self._anchor(rows, capitals)
        holdings = self._holdings(anchor.point)
        own = [holding for holding in holdings if holding.qid == qid]
        if not own:
            return {}, {}, anchor.place
        others = [holding for holding in holdings if holding.qid != qid and holding.years >= MIN_HOLD_YEARS]
        before = self._neighbour(others, own[0].start, lambda holding: holding.end, (None, qid), anchor)
        after = {} if self._partitioned(rows) else \
            self._neighbour(others, own[-1].end, lambda holding: holding.start, (qid, None), anchor)
        return before, after, anchor.place

    def _neighbour(self, others: list[Holding], moment: int, edge, pair, anchor: Anchor) -> dict:
        nearby = sorted((abs(edge(holding) - moment), holding.qid) for holding in others
                        if abs(edge(holding) - moment) <= WINDOW_YEARS)
        if not nearby:
            return {}
        neighbour = nearby[0][1]
        first, last = (neighbour, pair[1]) if pair[0] is None else (pair[0], neighbour)
        return {"id": neighbour, "region": anchor.region} if self.stated(first, last) else {}

    def _anchor(self, rows: gpd.GeoDataFrame, capitals: list[dict]) -> Anchor:
        held = []
        for capital in capitals:
            if capital["location"]:
                point = Point(capital["location"])
                near = rows[shapely.distance(rows.geometry.values, point) <= CAPITAL_TOLERANCE_DEGREES]
                if not near.empty:
                    held.append((int((near.ToYear - near.FromYear + 1).sum()), capital["name"], point, near))
        if held:
            _, place, point, near = max(held, key=lambda found: found[0])
            point = self._on_land(point, near)
        else:
            point, place = self._heartland(rows), ""
        return Anchor(point, place, self._country(point))

    @staticmethod
    def _on_land(point: Point, rows: gpd.GeoDataFrame) -> Point:
        """A capital on the water's edge, like Venice in its lagoon, moved onto the nearest land the maps give it."""
        if rows.geometry.contains(point).any():
            return point
        longest = rows.loc[(rows.ToYear - rows.FromYear).idxmax()]
        return longest.geometry.intersection(point.buffer(CAPITAL_TOLERANCE_DEGREES)).representative_point()

    @staticmethod
    def _heartland(rows: gpd.GeoDataFrame) -> Point:
        """The spot held for the most years; among ties, the one nearest their middle."""
        left, bottom, right, top = rows.total_bounds
        xs, ys = np.meshgrid(np.linspace(left, right, HEARTLAND_GRID), np.linspace(bottom, top, HEARTLAND_GRID))
        xs, ys = xs.ravel(), ys.ravel()
        years = np.zeros(xs.size)
        for geometry, start, end in zip(rows.geometry, rows.FromYear, rows.ToYear):
            years += shapely.contains_xy(geometry, xs, ys) * (end - start + 1)
        best = np.flatnonzero(years == years.max())
        middle_x, middle_y = xs[best].mean(), ys[best].mean()
        pick = best[np.argmin((xs[best] - middle_x) ** 2 + (ys[best] - middle_y) ** 2)]
        return Point(xs[pick], ys[pick])

    def _holdings(self, point: Point) -> list[Holding]:
        """Who held the point and when, with each holder's consecutive map snapshots joined into one stretch."""
        rows = self.frame[self.frame.geometry.contains(point)]
        holdings = []
        for qid, group in rows.groupby("qid"):
            stretches: list[list[int]] = []
            for start, end in sorted(zip(group.FromYear, group.ToYear)):
                if stretches and start <= stretches[-1][1] + HOLDING_GAP_YEARS:
                    stretches[-1][1] = max(stretches[-1][1], end)
                else:
                    stretches.append([start, end])
            holdings += [Holding(qid, int(start), int(end)) for start, end in stretches]
        return sorted(holdings, key=lambda holding: holding.start)

    def _partitioned(self, rows: gpd.GeoDataFrame) -> bool:
        """The year after it ended, its final territory was held in large parts by three or more states, as
        Poland-Lithuania's was in 1795."""
        last = rows.loc[rows.ToYear.idxmax()]
        shape = self.equal_area(gpd.GeoSeries([last.geometry], crs=rows.crs)).iloc[0]
        year = last.ToYear + 1
        later = self.frame[(self.frame.FromYear <= year) & (self.frame.ToYear >= year)
                           & (self.frame.qid != last.qid) & self.frame.geometry.intersects(last.geometry)]
        shares = {}
        for qid, group in self.equal_area(later).groupby("qid"):
            shares[qid] = max(geometry.intersection(shape).area for geometry in group.geometry) / shape.area
        return sum(share >= PARTITION_SHARE for share in shares.values()) >= PARTITION_HOLDERS

    def _country(self, point: Point) -> str:
        inside = self.countries[self.countries.geometry.contains(point)]
        if not inside.empty:
            return inside.iloc[0]["name"]
        return self.countries.iloc[int(np.argmin(shapely.distance(self.countries.geometry.values, point)))]["name"]
