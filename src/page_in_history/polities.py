import logging
import math
from collections import Counter
from dataclasses import asdict, dataclass, field

import geopandas as gpd
from shapely.geometry import Point

from page_in_history.dates import Span
from page_in_history.naming import slugify
from page_in_history.sources import vital
from page_in_history.sources.cliopatria import CAPITAL_TOLERANCE_DEGREES, EQUAL_AREA, Cliopatria
from page_in_history.sources.wikidata import Entity, Wikidata, entity_id
from page_in_history.sources.wikipedia import Infobox, Wikipedia, country_infobox, display_name, place_name
from page_in_history.succession import RegionalSuccession

POLITY_ROOTS = {
    "Q7275",  # state
    "Q96196009",  # former or current state
    "Q1048835",  # political territorial entity
    "Q1140229",  # polity
}
IDENTITY_PADDING_YEARS = 150
MIN_REGION_SHARE = 0.05
MIN_INSIDE_SHARE = 0.25
MIN_PEAK_AREA_KM2 = 50_000
MIN_LANGUAGE_EDITIONS = 60
MAX_VITAL_LEVEL = 4
LEVEL_5_LANGUAGES = 45
LEVEL_5_PEAK_KM2 = 1_000_000
MAX_DRAWN_SHARE = 0.5
LARGE_PEAK_KM2 = 2_000_000
LIVING_ON_MAP_BEFORE = 1900
LIVING_PEAK_KM2 = 2_000_000
STATE_KINDS = {"state", "historical country", "city-state", "empire", "kingdom"}
MAPLESS_KINDS = {
    "Q7275": "state", "Q3024240": "historical country", "Q133442": "city-state", "Q48349": "empire",
    "Q417175": "kingdom", "Q486972": "settlement", "Q10864048": "subdivision", "Q11514315": "period",
    "Q164950": "dynasty", "Q41710": "ethnic group", "Q8432": "civilization",
}
NOT_MAPLESS = {"subdivision", "period", "dynasty", "ethnic group", "civilization"}
KINDS = {
    "Q3624078": "sovereign state",
    "Q133442": "city-state",
    "Q486972": "settlement",
    "Q10864048": "subdivision",  # first-level administrative country subdivision
    "Q107390": "subdivision",  # federated state
    "Q133156": "colony",
    "Q164142": "colony",  # protectorate
    "Q161243": "colony",  # dependent territory
    "Q3024240": "historical",  # historical country
    "Q4204501": "historical",  # historical ethnic group
    "Q1620908": "historical",  # historical region
    "Q8432": "historical",  # civilization
    "Q28171280": "historical",  # ancient civilization
}
INFOBOX_TOLERANCE_YEARS = 1
AGE_TOLERANCE_SHARE = 0.01
PRESENT_YEAR = 2000
CLIOPATRIA_TOLERANCE_YEARS = 25
CAPITAL_MATCH_KM = 25
MODERN_SHARE_OF_POLITY = 0.05
MODERN_SHARE_OF_COUNTRY = 0.5

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Identity:
    name: str
    title: str
    qid: str
    how: str


@dataclass
class Period:
    start: Span | None
    end: Span | None
    confirmed: bool


@dataclass
class Polity:
    id: str
    slug: str
    name: str
    wikipedia: str
    map_year: int
    period: Period
    capitals: list[dict] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    rulers: list[dict] = field(default_factory=list)
    modern_countries: list[str] = field(default_factory=list)
    borders: bool = True
    map: str = ""
    checks: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


def equal_area(frame):
    """Reprojected for area measurements and repaired, since reprojection can make a valid shape self-intersect."""
    projected = frame.to_crs(EQUAL_AREA)
    return projected.set_geometry(projected.geometry.make_valid()) if hasattr(projected, "set_geometry") \
        else projected.make_valid()


def equal_area_shape(geometry, crs):
    return equal_area(gpd.GeoSeries([geometry], crs=crs)).iloc[0]


def years_overlap(entity: Entity, start: int, end: int) -> bool:
    """The item's dates overlap the years given; an item with a start but no end still exists."""
    inception = [year.earliest for year in entity.years("P571") + entity.years("P580")]
    dissolution = [year.latest for year in entity.years("P576") + entity.years("P582")]
    return bool(inception) and min(inception) <= end and (not dissolution or max(dissolution) >= start)


def kilometres(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Great-circle distance between two longitude and latitude points."""
    (lon1, lat1), (lon2, lat2) = (map(math.radians, point) for point in (first, second))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def reference(*dates) -> int:
    """A rough year for sizing the tolerance: the first date given, as a year, a Wikidata year or a span."""
    for date in dates:
        if isinstance(date, int):
            return date
        if date is not None:
            return date.earliest
    return PRESENT_YEAR


def tolerances(year: int) -> dict[frozenset, int]:
    """How far apart two sources may be and still agree on a date around the given year."""
    aged = max(INFOBOX_TOLERANCE_YEARS, round(AGE_TOLERANCE_SHARE * (PRESENT_YEAR - year)))
    return {
        frozenset(("infobox", "wikidata")): aged,
        frozenset(("infobox", "cliopatria")): max(CLIOPATRIA_TOLERANCE_YEARS, aged),
        frozenset(("wikidata", "cliopatria")): max(CLIOPATRIA_TOLERANCE_YEARS, aged),
    }


def vote(candidates: dict[str, Span | None], tolerances: dict[frozenset, int]) -> Span | None:
    """A date that another source confirms within that pair's tolerance. Cliopatria's snapshot years only confirm;
    the value shown comes from the most precise confirmed source among the others."""
    confirmed = [
        (source, span) for source, span in candidates.items()
        if span is not None and source != "cliopatria" and any(
            other != source and other_span is not None
            and span.overlaps(other_span, tolerances[frozenset((source, other))])
            for other, other_span in candidates.items()
        )
    ]
    return min(confirmed, key=lambda found: found[1].width)[1] if confirmed else None


class PolityCatalog:
    """Resolves every Cliopatria polity active around the window to the Wikidata item of its Wikipedia article."""

    def __init__(self, cliopatria: Cliopatria, wikipedia: Wikipedia, wikidata: Wikidata, start: int, end: int):
        self.cliopatria = cliopatria
        self.wikipedia = wikipedia
        self.wikidata = wikidata
        rows = cliopatria.active(start - IDENTITY_PADDING_YEARS, end + IDENTITY_PADDING_YEARS)
        links = {name: Counter(zip(group.Wikipedia, group.Wikidata)).most_common(1)[0][0]
                 for name, group in rows.groupby("Name")}
        pages = wikipedia.resolve([title for title, _ in links.values()])
        self.entities = wikidata.entities([page.qid for page in pages.values()])
        self.entities.update(wikidata.entities([qid for entity in self.entities.values() for qid in entity.ids("P17")]))
        classes = {qid for entity in self.entities.values() for qid in entity.ids("P31", preferred_only=False)}
        self.polity_classes = wikidata.subclass_of(sorted(classes), POLITY_ROOTS)
        others = [page.title for page in pages.values()
                  if page.qid in self.entities and not self.is_polity(self.entities[page.qid])]
        self.infoboxes = {title: country_infobox(text) for title, text in wikipedia.wikitext(others).items()}
        self.unresolved: dict[str, str] = {}
        candidates: dict[str, Identity] = {}
        years = {name: (int(group.FromYear.min()), int(group.ToYear.max())) for name, group in rows.groupby("Name")}
        for name, (title, cliopatria_qid) in links.items():
            page = pages.get(title)
            entity = self.entities.get(page.qid) if page and page.qid else None
            found = self._identify(name, page.title if page else title, entity, cliopatria_qid, *years[name])
            if isinstance(found, Identity):
                candidates[name] = found
            else:
                self.unresolved[name] = found
        candidates |= self._by_name({name: (links[name][1], *years[name]) for name in self.unresolved})
        for name in candidates:
            self.unresolved.pop(name, None)
        self.qids = {name: identity.qid for name, identity in candidates.items()}
        self.identities = self._without_duplicates(candidates)
        self.by_qid = {identity.qid: identity for identity in self.identities.values()}
        self.mapless: dict[str, tuple[int, int]] = {}

    def years(self, qid: str) -> tuple[int, int]:
        """When a civilization existed: Cliopatria's first and last year, or Wikidata's dates for one it does not draw."""
        if qid in self.mapless:
            return self.mapless[qid]
        rows = self.cliopatria.rows(self.by_qid[qid].name)
        return int(rows.FromYear.min()), int(rows.ToYear.max())

    def add_mapless(self, identity: Identity, entity: Entity, years: tuple[int, int]) -> None:
        self.entities[identity.qid] = entity
        self.identities[identity.name] = identity
        self.by_qid[identity.qid] = identity
        self.mapless[identity.qid] = years

    def _by_name(self, failed: dict[str, tuple[str, int, int]]) -> dict[str, Identity]:
        """Cliopatria's link sometimes points at the wrong article, such as the island for the Kingdom of Great
        Britain. Then its own Wikidata id, or a Wikidata state named exactly like the Cliopatria polity (by label or
        alias), stands in when its dates overlap and it is the only such state."""
        options = {name: [cliopatria_qid] + self.wikidata.exact_matches(name)
                   for name, (cliopatria_qid, _, _) in failed.items()}
        entities = self.wikidata.entities([qid for qids in options.values() for qid in qids])
        classes = {qid for entity in entities.values() for qid in entity.ids("P31", preferred_only=False)}
        self.polity_classes |= self.wikidata.subclass_of(sorted(classes - self.polity_classes), POLITY_ROOTS)
        found = {}
        for name, qids in options.items():
            _, start, end = failed[name]
            states = {qid: entities[qid] for qid in qids if qid in entities and self.is_polity(entities[qid])
                      and entities[qid].enwiki and years_overlap(entities[qid], start, end)}
            if len(states) == 1:
                entity = next(iter(states.values()))
                self.entities[entity.id] = entity
                found[name] = Identity(name, entity.enwiki, entity.id, "Wikidata state named like the Cliopatria polity")
        return found

    def is_polity(self, entity: Entity) -> bool:
        return bool(set(entity.ids("P31", preferred_only=False)) & self.polity_classes)

    def _identify(self, name: str, title: str, entity: Entity | None, cliopatria_qid: str,
                  start: int, end: int) -> Identity | str:
        if entity is None:
            return f"Wikipedia link {title!r} has no Wikidata item"
        if self.is_polity(entity):
            if entity.id == cliopatria_qid:
                return Identity(name, entity.enwiki or title, entity.id, "same item in Cliopatria and Wikipedia")
            if years_overlap(entity, start, end):
                return Identity(name, entity.enwiki or title, entity.id, "Wikipedia item with overlapping dates")
            return f"{entity.id} from {title!r} disagrees with {cliopatria_qid} and its dates don't overlap"
        dynasty = (entity.label or "").split(" ")[0].lower()
        states = [self.entities[qid] for qid in entity.ids("P17") if qid in self.entities
                  and self.is_polity(self.entities[qid]) and years_overlap(self.entities[qid], start, end)
                  and dynasty in (self.entities[qid].label or "").lower().split()]
        if len(states) == 1 and states[0].enwiki:
            return Identity(name, states[0].enwiki, states[0].id, f"the state named after {entity.label}")
        infobox = self.infoboxes.get(title)
        if infobox and self._infobox_overlaps(infobox, entity, start, end):
            return Identity(name, title, entity.id, "Wikipedia article with a country infobox")
        return f"Wikipedia link {title!r} is not a polity article"

    @staticmethod
    def _infobox_overlaps(infobox: Infobox, entity: Entity, start: int, end: int) -> bool:
        """Wikipedia's editors file the article as a state; its dates, or the item's, must overlap Cliopatria's."""
        if infobox.start and infobox.end:
            return (infobox.start.earliest <= end + CLIOPATRIA_TOLERANCE_YEARS
                    and infobox.end.latest >= start - CLIOPATRIA_TOLERANCE_YEARS)
        return years_overlap(entity, start, end)

    def _without_duplicates(self, candidates: dict[str, Identity]) -> dict[str, Identity]:
        """When several Cliopatria names share one item, the one with the largest territory keeps it."""
        by_qid: dict[str, list[Identity]] = {}
        for identity in candidates.values():
            by_qid.setdefault(identity.qid, []).append(identity)
        kept = {}
        for identities in by_qid.values():
            largest = max(identities, key=lambda identity: self.cliopatria.rows(identity.name).Area.max())
            kept[largest.name] = largest
            for other in identities:
                if other is not largest:
                    self.unresolved[other.name] = f"shares {largest.qid} with {largest.name!r}"
        return kept


class PolityBuilder:
    def __init__(self, catalog: PolityCatalog, countries: gpd.GeoDataFrame):
        self.catalog = catalog
        self.cliopatria = catalog.cliopatria
        self.wikipedia = catalog.wikipedia
        self.wikidata = catalog.wikidata
        self.countries = equal_area(countries)
        self.succession = RegionalSuccession(self.cliopatria.frame, catalog.qids, countries, self._stated, equal_area)

    def select(self, region: str, start: int, end: int) -> list[Identity]:
        area = self.countries[self.countries.name == region].geometry.union_all()
        rows = equal_area(self.cliopatria.active(start, end))
        selected = []
        for name, group in rows.groupby("Name"):
            if name not in self.catalog.identities or group.Area.max() < MIN_PEAK_AREA_KM2:
                continue
            inside = group.geometry.intersection(area).area
            if (inside / area.area).max() >= MIN_REGION_SHARE or (inside / group.geometry.area).max() >= MIN_INSIDE_SHARE:
                selected.append(self.catalog.identities[name])
        return sorted(selected, key=lambda identity: self.cliopatria.rows(identity.name).FromYear.min())

    def select_notable(self) -> list[Identity]:
        """Civilizations that Wikipedia's editors list as vital, that Wikipedia covers in many languages, or that ruled
        a vast territory. Any one is enough, so small early civilizations and large regional empires both qualify."""
        identities = list(self.catalog.identities.values())
        qids = [identity.qid for identity in identities]
        editions = self.wikidata.language_editions(qids)
        levels = self._vital_levels()
        kinds = self.wikidata.kinds(qids, KINDS)
        selected = [identity for identity in identities
                    if self._notable(identity, editions.get(identity.qid, 0), levels.get(identity.qid), kinds[identity.qid])]
        selected += self._mapless(levels, selected)
        return sorted(selected, key=lambda identity: self.catalog.years(identity.qid)[0])

    def _mapless(self, levels: dict[str, int], drawn: list[Identity]) -> list[Identity]:
        """States Wikipedia's editors list as vital that Cliopatria does not draw, such as Classical Athens or Sparta,
        chosen by the same signals but needing both dates, from Wikidata or else the Wikipedia infobox, since there
        are no borders to date them. City-states count although Wikidata also files them as settlements. Left out:
        items Wikidata files as a civilization (Ancient Egypt) and umbrellas whose parts are drawn (Ancient Rome),
        since the drawn states stand in for them, and a state whose capital, or its own location, lies inside drawn
        civilizations of the deck for most of its years: London for the United Kingdom of Great Britain and Ireland,
        or the city of Babylon inside Babylonia. They get no map; figures can belong to them."""
        qids = [qid for qid in levels if qid not in self.catalog.by_qid]
        entities = self.wikidata.entities(qids)
        kinds = self.wikidata.kinds(list(entities), MAPLESS_KINDS)
        editions = self.wikidata.language_editions(list(entities))
        drawn_parts = {whole for state in self.catalog.by_qid
                       for whole in self.catalog.entities[state].ids("P361", preferred_only=False)}
        states = {qid: entity for qid, entity in entities.items()
                  if (kinds.get(qid, set()) & STATE_KINDS) and not (kinds.get(qid, set()) & NOT_MAPLESS)
                  and ("settlement" not in kinds.get(qid, set()) or "city-state" in kinds[qid])
                  and entity.enwiki and qid not in drawn_parts
                  and not any(part in self.catalog.by_qid for part in entity.ids("P527", preferred_only=False))
                  and (levels[qid] <= MAX_VITAL_LEVEL or editions.get(qid, 0) >= LEVEL_5_LANGUAGES)}
        texts = self.wikipedia.wikitext([entity.enwiki for entity in states.values()])
        drawn_frame = self.cliopatria.frame[self.cliopatria.frame.Name.isin({identity.name for identity in drawn})]
        capitals = self.wikidata.entities([capital for entity in states.values() for capital in entity.ids("P36")])
        found = []
        for qid, entity in states.items():
            infobox = country_infobox(texts.get(entity.enwiki, "")) or Infobox()
            starts = [year.earliest for year in entity.years("P571") + entity.years("P580")]
            ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
            first = min(starts) if starts else infobox.start.earliest if infobox.start else None
            last = max(ends) if ends else infobox.end.latest if infobox.end else None
            name = display_name(entity.enwiki)
            if first is None or last is None or name in self.catalog.identities:
                continue
            location = next((capitals[c].coordinates() for c in entity.ids("P36") if c in capitals and capitals[c].coordinates()),
                            entity.coordinates())
            if location and self._drawn_share(drawn_frame, location, first, last) > MAX_DRAWN_SHARE:
                continue
            identity = Identity(name, entity.enwiki, qid, "Vital Articles state without Cliopatria borders")
            self.catalog.add_mapless(identity, entity, (first, last))
            found.append(identity)
        return found

    @staticmethod
    def _drawn_share(frame: gpd.GeoDataFrame, location: tuple[float, float], first: int, last: int) -> float:
        """The share of the years from first to last in which a drawn civilization covers the location."""
        rows = frame[frame.geometry.contains(Point(location))]
        years = {year for start, end in zip(rows.FromYear, rows.ToYear)
                 for year in range(max(int(start), first), min(int(end), last) + 1)}
        return len(years) / (last - first + 1)

    def _vital_levels(self) -> dict[str, int]:
        levels = vital.article_levels(self.wikipedia.http, self.wikipedia)
        pages = self.wikipedia.resolve(list(levels))
        found: dict[str, int] = {}
        for title, level in levels.items():
            page = pages.get(title)
            if page and page.qid:
                found[page.qid] = min(level, found.get(page.qid, level))
        return found

    def _notable(self, identity: Identity, languages: int, level: int | None, kinds: set[str]) -> bool:
        """Listed as vital at level 4 or above, in at least 60 languages, at level 5 with 45 languages or a peak of
        1 million km², or at a peak of 2 million km² without being a colony. A sovereign state that still exists
        qualifies only if it was on the maps before 1900 and reached 2 million km², as the United States did. Any
        other polity without an end date must be filed as historical, since Cliopatria sometimes links an old polity
        to today's place, such as the island of Rhodes, and today's provinces and towns never qualify."""
        entity = self.catalog.entities[identity.qid]
        rows = self.cliopatria.rows(identity.name)
        peak = rows.Area.max()
        if not entity.years("P576") + entity.years("P582"):
            if "subdivision" in kinds or ("settlement" in kinds and "city-state" not in kinds):
                return False
            if "sovereign state" in kinds:
                if rows.FromYear.min() >= LIVING_ON_MAP_BEFORE or peak < LIVING_PEAK_KM2:
                    return False
            elif "historical" not in kinds:
                return False
        level = level or MAX_VITAL_LEVEL + 2
        return (level <= MAX_VITAL_LEVEL or languages >= MIN_LANGUAGE_EDITIONS
                or (level == MAX_VITAL_LEVEL + 1 and (languages >= LEVEL_5_LANGUAGES or peak >= LEVEL_5_PEAK_KM2))
                or (peak >= LARGE_PEAK_KM2 and "colony" not in kinds))

    def build(self, identities: list[Identity]) -> list[Polity]:
        """Notes for the selected civilizations. A civilization before or after one of them may come from outside
        the deck, as any resolved civilization can be the answer."""
        known = list(self.catalog.identities.values())
        texts = self.wikipedia.wikitext([identity.title for identity in known])
        self.infoboxes = {identity.qid: country_infobox(texts.get(identity.title, "")) or Infobox() for identity in known}
        linked = [title for box in self.infoboxes.values()
                  for title in box.capital + box.predecessors + box.successors + box.leaders]
        self.pages = self.wikipedia.resolve(linked)
        referenced = [self.catalog.entities[identity.qid].ids("P36") for identity in known]
        self.names = self.wikidata.entities([qid for ids in referenced for qid in ids] +
                                            [page.qid for page in self.pages.values() if page.qid])
        return [self._polity(identity, self.infoboxes[identity.qid]) for identity in identities]

    def _stated(self, first: str, last: str) -> bool:
        """Wikidata or a Wikipedia infobox, on either civilization's side, says that the second followed the first."""
        earlier, later = self.catalog.entities.get(first), self.catalog.entities.get(last)
        return bool(
            (earlier and last in earlier.ids("P1366", "P156"))
            or (later and first in later.ids("P1365", "P155"))
            or (first in self.infoboxes and last in self._qids(self.infoboxes[first].successors))
            or (last in self.infoboxes and first in self._qids(self.infoboxes[last].predecessors))
        )

    def _qids(self, titles: list[str]) -> list[str]:
        return list(dict.fromkeys(self.pages[title].qid for title in titles if title in self.pages and self.pages[title].qid))

    def _named(self, neighbour: dict) -> dict:
        return {**neighbour, "name": self._name(neighbour["id"])} if neighbour else {}

    def _capitals(self, entity: Entity, infobox: Infobox) -> list[dict]:
        """Capitals that Wikidata and the infobox both name: the same item, or two items for the same place within
        25 km, as when Wikidata names the prefecture Shuntian Fu and the infobox the city of Beijing. They are shown
        under the infobox's name, once per place (Bursa, not also Prusa), and a linked state is never a capital."""
        listed = entity.ids("P36")
        places = [found.coordinates() for found in (self.names.get(qid) for qid in listed) if found and found.coordinates()]
        capitals = []
        for qid in self._qids(infobox.capital):
            if qid in self.catalog.by_qid or (qid in self.names and self.catalog.is_polity(self.names[qid])):
                continue
            capital = self._capital(qid)
            location = capital["location"]
            nearby = bool(location) and any(kilometres(location, place) <= CAPITAL_MATCH_KM for place in places)
            repeated = bool(location) and any(found["location"] and kilometres(location, found["location"]) <= CAPITAL_MATCH_KM
                                              for found in capitals)
            if (qid in listed or nearby) and not repeated and capital["name"] not in {found["name"] for found in capitals}:
                capitals.append(capital)
        return capitals

    def _capital(self, qid: str) -> dict:
        entity = self.names.get(qid)
        location = entity.coordinates() if entity else None
        return {"name": place_name(self._name(qid)), "location": list(location) if location else None}

    def _name(self, qid: str) -> str:
        if qid in self.catalog.by_qid:
            return self.catalog.by_qid[qid].name
        entity = self.names.get(qid)
        return display_name(entity.enwiki) if entity and entity.enwiki else (entity.label if entity and entity.label else qid)

    def _polity(self, identity: Identity, infobox: Infobox) -> Polity:
        entity = self.catalog.entities[identity.qid]
        rows = self.cliopatria.rows(identity.name)
        drawn = not rows.empty
        first, last = self.catalog.years(identity.qid)
        period = self._period(entity, infobox, first if drawn else None, last if drawn else None)
        capitals = self._capitals(entity, infobox)
        parts = [self.catalog.by_qid[qid].name for qid in entity.ids("P527", preferred_only=False)
                 if qid in self.catalog.by_qid and qid != identity.qid]
        map_year, extent = self._peak(identity.name, parts, rows, period, capitals) if drawn else (first, None)
        before, after, home = self.succession.neighbours(identity.qid, capitals)
        polity = Polity(
            id=identity.qid,
            slug=slugify(identity.name),
            name=identity.name,
            wikipedia=identity.title,
            map_year=map_year,
            period=period,
            capitals=capitals,
            parts=parts,
            before=self._named(before),
            after=self._named(after),
            rulers=self._rulers(identity.qid, self._qids(infobox.leaders)),
            modern_countries=self._modern_countries(extent) if drawn else [],
            borders=drawn,
        )
        polity.checks = {
            "infobox": bool(infobox.start or infobox.capital or infobox.leaders),
            "capital_sources": {"wikidata": [self._name(q) for q in entity.ids("P36")],
                                "infobox": [self._name(q) for q in self._qids(infobox.capital)]},
            "cliopatria_years": [first, last] if drawn else [],
            "identity": identity.how,
            "home": home or "the spot held longest",
        }
        return polity

    def _peak(self, name: str, parts: list[str], rows: gpd.GeoDataFrame, period: Period, capitals: list[dict]):
        """The year of the largest extent, looking only inside the confirmed period when there is one, counted with the
        parts Wikidata lists (Spain for the Spanish Empire) and preferring years when the civilization holds a capital,
        so that a year of foreign occupation is not shown; with that year's territory."""
        if period.confirmed:
            within = rows[(rows.ToYear >= period.start.earliest) & (rows.FromYear <= period.end.latest)]
            rows = within if not within.empty else rows
        points = [Point(capital["location"]) for capital in capitals if capital["location"]]
        snapshots = []
        for year in sorted({int(year) for year in rows.FromYear}):
            territory = self.cliopatria.at(year)
            territory = territory[territory.Name.isin([name, *parts])]
            shape = territory.geometry.union_all()
            holds = any(shape.buffer(CAPITAL_TOLERANCE_DEGREES).contains(point) for point in points)
            snapshots.append((holds, float(territory.Area.sum()), year, shape))
        _, _, year, shape = max(snapshots, key=lambda snapshot: snapshot[:3])
        return year, shape

    def _period(self, entity: Entity, infobox: Infobox, clio_start: int | None, clio_end: int | None) -> Period:
        """Each end needs two sources to agree. Wikidata's inception or start time and dissolution or end time all
        count. The infobox and Wikidata agree within a year for modern dates and within about 1% of a date's age for
        older ones, since ancient chronologies differ by decades: 27 years at 700 BC, 47 at 2700 BC."""
        inception = min(entity.years("P571") + entity.years("P580"), key=lambda year: year.earliest, default=None)
        dissolution = max(entity.years("P576") + entity.years("P582"), key=lambda year: year.latest, default=None)
        start = vote({"infobox": infobox.start, "wikidata": inception.as_span() if inception else None,
                      "cliopatria": Span(clio_start, clio_start) if clio_start is not None else None},
                     tolerances(reference(clio_start, inception, infobox.start)))
        end = vote({"infobox": infobox.end, "wikidata": dissolution.as_span() if dissolution else None,
                    "cliopatria": Span(clio_end, clio_end) if clio_end is not None else None},
                   tolerances(reference(clio_end, dissolution, infobox.end)))
        return Period(start, end, start is not None and end is not None)

    def _rulers(self, polity_qid: str, leaders: list[str]) -> list[dict]:
        """Infobox leaders that Wikidata also records as holding an office of the polity."""
        if not leaders:
            return []
        query = f"""SELECT DISTINCT ?person WHERE {{
          {{ ?office wdt:P1001 wd:{polity_qid} }} UNION {{ wd:{polity_qid} wdt:P1906 ?office }}
          UNION {{ ?office wdt:P17 wd:{polity_qid} }}
          ?person wdt:P39 ?office .
        }}"""
        holders = {entity_id(row["person"]) for row in self.wikidata.query(query)}
        return [{"id": qid, "name": self._name(qid)} for qid in leaders if qid in holders]

    def _modern_countries(self, geometry) -> list[str]:
        shape = equal_area_shape(geometry, self.cliopatria.frame.crs)
        found = []
        for country in self.countries[self.countries.listed & self.countries.intersects(shape)].itertuples():
            overlap = country.geometry.intersection(shape).area
            if overlap / shape.area >= MODERN_SHARE_OF_POLITY or overlap / country.geometry.area >= MODERN_SHARE_OF_COUNTRY:
                found.append((overlap, country.name))
        return [name for _, name in sorted(found, reverse=True)]
