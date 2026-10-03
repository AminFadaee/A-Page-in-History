import logging
import zipfile
from dataclasses import dataclass

import geopandas as gpd
from shapely.validation import make_valid

from page_in_history.http import Http

URL = "https://github.com/Seshat-Global-History-Databank/cliopatria/raw/main/cliopatria.geojson.zip"
EQUAL_AREA = "EPSG:6933"
SPIKE_RATIO = 2.5

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Spike:
    name: str
    from_year: int
    to_year: int
    area_km2: float
    neighbour_area_km2: float


class Cliopatria:
    """Polity shapes by time step. Parenthesised groupings and alliances are left out."""

    def __init__(self, frame: gpd.GeoDataFrame):
        self.frame = frame
        self.spikes = self._drop_spikes()

    @classmethod
    def load(cls, http: Http) -> "Cliopatria":
        archive = http.download(URL, "cliopatria.geojson.zip")
        with zipfile.ZipFile(archive) as zipped:
            member = next(name for name in zipped.namelist() if name.endswith(".geojson"))
        frame = gpd.read_file(f"zip://{archive}!{member}")
        frame = frame[(frame.Type == "POLITY") & ~frame.Name.str.startswith("(")].copy()
        frame["geometry"] = frame.geometry.apply(make_valid).buffer(0)
        frame["FromYear"] = frame.FromYear.astype(int)
        frame["ToYear"] = frame.ToYear.astype(int)
        return cls(frame.sort_values(["Name", "FromYear"]).reset_index(drop=True))

    def active(self, start: int, end: int) -> gpd.GeoDataFrame:
        return self.frame[(self.frame.ToYear >= start) & (self.frame.FromYear <= end)]

    def at(self, year: int) -> gpd.GeoDataFrame:
        return self.frame[(self.frame.FromYear <= year) & (self.frame.ToYear >= year)]

    def rows(self, name: str) -> gpd.GeoDataFrame:
        return self.frame[self.frame.Name == name]

    def _drop_spikes(self) -> list[Spike]:
        """A single time step far larger than both neighbours is a mislabelled shape, not a conquest."""
        spikes: list[Spike] = []
        drop = []
        for name, rows in self.frame.groupby("Name", sort=False):
            areas = rows.Area.tolist()
            for position in range(1, len(areas) - 1):
                neighbour = max(areas[position - 1], areas[position + 1])
                if areas[position] > SPIKE_RATIO * neighbour:
                    row = rows.iloc[position]
                    drop.append(rows.index[position])
                    spikes.append(Spike(name, int(row.FromYear), int(row.ToYear), float(row.Area), float(neighbour)))
        for spike in spikes:
            logger.warning("Ignoring %s %d..%d: %.0f km² against %.0f km² either side",
                           spike.name, spike.from_year, spike.to_year, spike.area_km2, spike.neighbour_area_km2)
        self.frame = self.frame.drop(index=drop)
        return spikes
