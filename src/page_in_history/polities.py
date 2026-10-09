import logging
from collections import Counter
from dataclasses import asdict, dataclass, field

import geopandas as gpd

from page_in_history.dates import Span
from page_in_history.naming import slugify
from page_in_history.sources.cliopatria import EQUAL_AREA, Cliopatria
from page_in_history.sources.wikidata import Entity, Wikidata, entity_id
from page_in_history.sources.wikipedia import Infobox, Wikipedia, country_infobox, display_name, place_name

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
ENDED_BY = 1950
HISTORICAL_CLASSES = {
    "Q3024240",  # historical country
    "Q4204501",  # historical ethnic group
    "Q1620908",  # historical region
    "Q8432",  # civilization
    "Q28171280",  # ancient civilization
}
HUMAN_SETTLEMENT = "Q486972"
INFOBOX_TOLERANCE_YEARS = 1
CLIOPATRIA_TOLERANCE_YEARS = 25
SUCCESSION_WINDOW_YEARS = 30
SUCCESSION_OVERLAP = 0.2
MODERN_SHARE_OF_POLITY = 0.05
MODERN_SHARE_OF_COUNTRY = 0.5
MIN_HANDOVER_KM2 = 20_000
HANDOVER_COUNTRY_SHARE = 0.1
HANDOVER_COUNTRIES = 3

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
    predecessors: list[str] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)
    succession: dict = field(default_factory=dict)
    rulers: list[dict] = field(default_factory=list)
    modern_countries: list[str] = field(default_factory=list)
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


def shortcut(link: tuple[str, str], links: set[tuple[str, str]]) -> bool:
    """A link that skips a confirmed middle step: A→B when A→C and C→B are also confirmed."""
    first, last = link
    middles = {after for before, after in links if before == first} & {before for before, after in links if after == last}
    return bool(middles - {first, last})


def succession_graph(qid: str, links: list[tuple[str, str]], built: set[str], anchor) -> dict:
    """The civilization's neighbourhood in the succession graph: what came directly before and after it, and those
    neighbours' other links, so the civilization is the only unknown node. Every arrow carries the region where the
    handover happened. A neighbour is shown only when all its arrows can be placed, and the card needs a neighbour
    whose own links are all known, otherwise the picture could fit another civilization too."""
    def complete(neighbour: str, side: int) -> list[tuple[str, str]] | None:
        own = (neighbour, qid) if side == 0 else (qid, neighbour)
        if not anchor(*own):
            return None
        if neighbour not in built:
            return [own]
        siblings = [link for link in links if (link[0] == neighbour if side == 0 else link[1] == neighbour)]
        return siblings if all(anchor(*link) for link in siblings) else None

    layers, edges = {qid: 1}, []
    for side, neighbours in ((0, [first for first, last in links if last == qid]),
                             (2, [last for first, last in links if first == qid])):
        for neighbour in neighbours:
            shown = complete(neighbour, side)
            if shown is None:
                continue
            layers[neighbour] = side
            for first, last in shown:
                layers.setdefault(last if side == 0 else first, 1)
                edges.append((first, last))
    if not set(layers) - {qid} & built:
        return {}
    return {"nodes": [{"id": node, "layer": layer} for node, layer in layers.items()],
            "edges": [[first, last, anchor(first, last)] for first, last in dict.fromkeys(edges)]}


def years_overlap(entity: Entity, start: int, end: int) -> bool:
    inception = [year.earliest for year in entity.years("P571") + entity.years("P580")]
    dissolution = [year.latest for year in entity.years("P576") + entity.years("P582")]
    return bool(inception and dissolution) and min(inception) <= end and max(dissolution) >= start


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
        for name, (title, cliopatria_qid) in links.items():
            group = rows[rows.Name == name]
            page = pages.get(title)
            entity = self.entities.get(page.qid) if page and page.qid else None
            found = self._identify(name, page.title if page else title, entity, cliopatria_qid,
                                   int(group.FromYear.min()), int(group.ToYear.max()))
            if isinstance(found, Identity):
                candidates[name] = found
            else:
                self.unresolved[name] = found
        self.identities = self._without_duplicates(candidates)
        self.by_qid = {identity.qid: identity for identity in self.identities.values()}

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
        self.handovers: dict[tuple[str, str], str] = {}

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
        """Civilizations covered by Wikipedia in at least 60 languages that ended by 1950: importance rather than size,
        so small early civilizations stay in and today's countries are left to a geography deck."""
        editions = self.wikidata.language_editions([identity.qid for identity in self.catalog.identities.values()])
        popular = [identity for identity in self.catalog.identities.values()
                   if editions.get(identity.qid, 0) >= MIN_LANGUAGE_EDITIONS]
        classes = {qid for identity in popular for qid in self.catalog.entities[identity.qid].ids("P31", preferred_only=False)}
        self.settlement_classes = self.wikidata.subclass_of(sorted(classes), {HUMAN_SETTLEMENT})
        selected = [identity for identity in popular if self._ended(self.catalog.entities[identity.qid])]
        return sorted(selected, key=lambda identity: self.cliopatria.rows(identity.name).FromYear.min())

    def _ended(self, entity: Entity) -> bool:
        """The item itself ended by 1950; or, when Wikidata gives no end date, it files the item as a historical
        country, people, region or civilization and not as a settlement. Cliopatria's own dates are not enough: it
        sometimes links a historical polity to today's place, such as San Marino or the city of Kathmandu."""
        ends = [year.latest for year in entity.years("P576") + entity.years("P582")]
        if ends:
            return max(ends) <= ENDED_BY
        classes = set(entity.ids("P31", preferred_only=False))
        return bool(classes & HISTORICAL_CLASSES) and not classes & self.settlement_classes

    def build(self, identities: list[Identity]) -> list[Polity]:
        """Notes for the selected civilizations. Successions are worked out over every resolved civilization, so a
        civilization left out of the deck can still appear in a selected one's diagram, just never be asked about."""
        known = list(self.catalog.identities.values())
        texts = self.wikipedia.wikitext([identity.title for identity in known])
        infoboxes = {identity.name: country_infobox(texts.get(identity.title, "")) or Infobox() for identity in known}
        linked = [title for box in infoboxes.values() for title in box.capital + box.predecessors + box.successors + box.leaders]
        self.pages = self.wikipedia.resolve(linked)
        referenced = [entity.ids(*props) for entity in (self.catalog.entities[i.qid] for i in known)
                      for props in (("P36",), ("P1365", "P155"), ("P1366", "P156"))]
        self.names = self.wikidata.entities([qid for ids in referenced for qid in ids] +
                                            [page.qid for page in self.pages.values() if page.qid])
        links = self._succession_links(known, infoboxes)
        resolved = {identity.qid for identity in known}
        return [self._polity(identity, infoboxes[identity.name], links, resolved) for identity in identities]

    def _succession_links(self, identities: list[Identity], infoboxes: dict[str, Infobox]) -> list[tuple[str, str]]:
        """'A was followed by B' is one fact, whichever article states it. Each source counts once per link, from
        either side, and a link holds when at least two of Wikidata, Wikipedia and Cliopatria's shapes agree."""
        evidence: dict[tuple[str, str], set[str]] = {}
        for identity in identities:
            entity, infobox = self.catalog.entities[identity.qid], infoboxes[identity.name]
            rows = self.cliopatria.rows(identity.name)
            before = {"wikidata": entity.ids("P1365", "P155"), "wikipedia": self._qids(infobox.predecessors),
                      "cliopatria": self._spatial(rows.iloc[0], before=True)}
            after = {"wikidata": entity.ids("P1366", "P156"), "wikipedia": self._qids(infobox.successors),
                     "cliopatria": self._spatial(rows.iloc[-1], before=False)}
            for source, qids in before.items():
                for qid in qids:
                    evidence.setdefault((qid, identity.qid), set()).add(source)
            for source, qids in after.items():
                for qid in qids:
                    evidence.setdefault((identity.qid, qid), set()).add(source)
        confirmed = {link for link, sources in evidence.items() if len(sources) >= 2 and link[0] != link[1]}
        return [link for link in evidence if link in confirmed and not shortcut(link, confirmed)]

    def _qids(self, titles: list[str]) -> list[str]:
        return list(dict.fromkeys(self.pages[title].qid for title in titles if title in self.pages and self.pages[title].qid))

    def _handover(self, first: str, last: str) -> str:
        if (first, last) not in self.handovers:
            self.handovers[first, last] = self._handover_region(first, last)
        return self.handovers[first, last]

    def _handover_region(self, first: str, last: str) -> str:
        """Where one civilization followed the other: the land both held within the succession window around the end
        of the first and the start of the second, named by today's largest countries in it."""
        if first not in self.catalog.by_qid or last not in self.catalog.by_qid:
            return ""
        before = self.cliopatria.rows(self.catalog.by_qid[first].name)
        after = self.cliopatria.rows(self.catalog.by_qid[last].name)
        shared = None
        for moment in (int(before.ToYear.max()), int(after.FromYear.min())):
            window = (moment - SUCCESSION_WINDOW_YEARS, moment + SUCCESSION_WINDOW_YEARS)
            old = before[(before.ToYear >= window[0]) & (before.FromYear <= window[1])]
            new = after[(after.ToYear >= window[0]) & (after.FromYear <= window[1])]
            if old.empty or new.empty:
                continue
            overlap = (equal_area_shape(old.geometry.union_all(), old.crs)
                       .intersection(equal_area_shape(new.geometry.union_all(), new.crs)))
            if shared is None or overlap.area > shared.area:
                shared = overlap
        if shared is None or shared.area < MIN_HANDOVER_KM2 * 1e6:
            return ""
        found = [(country.geometry.intersection(shared).area, country.name)
                 for country in self.countries[self.countries.intersects(shared)].itertuples()]
        names = [name for area, name in sorted(found, reverse=True) if area / shared.area >= HANDOVER_COUNTRY_SHARE]
        return ", ".join(names[:HANDOVER_COUNTRIES])

    def _named(self, graph: dict) -> dict:
        for node in graph.get("nodes", []):
            node["name"] = self._name(node["id"])
        return graph

    def _capital(self, qid: str) -> dict:
        entity = self.names.get(qid)
        location = entity.coordinates() if entity else None
        return {"name": place_name(self._name(qid)), "location": list(location) if location else None}

    def _name(self, qid: str) -> str:
        if qid in self.catalog.by_qid:
            return self.catalog.by_qid[qid].name
        entity = self.names.get(qid)
        return display_name(entity.enwiki) if entity and entity.enwiki else (entity.label if entity and entity.label else qid)

    def _polity(self, identity: Identity, infobox: Infobox, links: list[tuple[str, str]], built: set[str]) -> Polity:
        entity = self.catalog.entities[identity.qid]
        rows = self.cliopatria.rows(identity.name)
        period = self._period(entity, infobox, int(rows.FromYear.min()), int(rows.ToYear.max()))
        peak = self._peak(rows, period)
        capitals = [qid for qid in entity.ids("P36") if qid in self._qids(infobox.capital)]
        predecessors = [before for before, after in links if after == identity.qid]
        successors = [after for before, after in links if before == identity.qid]
        polity = Polity(
            id=identity.qid,
            slug=slugify(identity.name),
            name=identity.name,
            wikipedia=identity.title,
            map_year=int(peak.FromYear),
            period=period,
            capitals=[self._capital(qid) for qid in capitals],
            predecessors=[self._name(qid) for qid in predecessors],
            successors=[self._name(qid) for qid in successors],
            succession=self._named(succession_graph(identity.qid, links, built, self._handover)),
            rulers=self._rulers(identity.qid, self._qids(infobox.leaders)),
            modern_countries=self._modern_countries(peak.geometry),
        )
        polity.checks = {
            "infobox": bool(infobox.start or infobox.capital or infobox.leaders),
            "capital_sources": {"wikidata": [self._name(q) for q in entity.ids("P36")],
                                "infobox": [self._name(q) for q in self._qids(infobox.capital)]},
            "cliopatria_years": [int(rows.FromYear.min()), int(rows.ToYear.max())],
            "identity": identity.how,
        }
        return polity

    @staticmethod
    def _peak(rows: gpd.GeoDataFrame, period: Period):
        """The largest extent, looking only inside the confirmed period when there is one."""
        if period.confirmed:
            within = rows[(rows.ToYear >= period.start.earliest) & (rows.FromYear <= period.end.latest)]
            rows = within if not within.empty else rows
        return rows.loc[rows.Area.idxmax()]

    def _period(self, entity: Entity, infobox: Infobox, clio_start: int, clio_end: int) -> Period:
        tolerances = {
            frozenset(("infobox", "wikidata")): INFOBOX_TOLERANCE_YEARS,
            frozenset(("infobox", "cliopatria")): CLIOPATRIA_TOLERANCE_YEARS,
            frozenset(("wikidata", "cliopatria")): CLIOPATRIA_TOLERANCE_YEARS,
        }
        inception = min(entity.years("P571"), key=lambda year: year.earliest, default=None)
        dissolution = max(entity.years("P576"), key=lambda year: year.latest, default=None)
        start = vote({"infobox": infobox.start, "wikidata": inception.as_span() if inception else None,
                      "cliopatria": Span(clio_start, clio_start)}, tolerances)
        end = vote({"infobox": infobox.end, "wikidata": dissolution.as_span() if dissolution else None,
                    "cliopatria": Span(clio_end, clio_end)}, tolerances)
        return Period(start, end, start is not None and end is not None)

    def _spatial(self, row, before: bool) -> list[str]:
        year = row.FromYear if before else row.ToYear
        window = (year - SUCCESSION_WINDOW_YEARS, year) if before else (year, year + SUCCESSION_WINDOW_YEARS)
        others = self.cliopatria.frame
        column = others.ToYear if before else others.FromYear
        nearby = others[(column >= window[0]) & (column <= window[1]) & (others.Name != row.Name)]
        nearby = nearby[nearby.geometry.intersects(row.geometry)]
        shape = equal_area_shape(row.geometry, others.crs)
        found = []
        for name, group in equal_area(nearby).groupby("Name"):
            overlap = max(geometry.intersection(shape).area for geometry in group.geometry) / shape.area
            if overlap >= SUCCESSION_OVERLAP and name in self.catalog.identities:
                found.append(self.catalog.identities[name].qid)
        return found

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
        for country in self.countries[self.countries.intersects(shape)].itertuples():
            overlap = country.geometry.intersection(shape).area
            if overlap / shape.area >= MODERN_SHARE_OF_POLITY or overlap / country.geometry.area >= MODERN_SHARE_OF_COUNTRY:
                found.append((overlap, country.name))
        return [name for _, name in sorted(found, reverse=True)]
