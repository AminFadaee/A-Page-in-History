from dataclasses import dataclass

from page_in_history.dates import Span
from page_in_history.http import Http

API = "https://www.wikidata.org/w/api.php"
SPARQL = "https://query.wikidata.org/sparql"
BATCH = 20
CIRCA = "Q5727902"
SOURCING_CIRCUMSTANCES = "P1480"
YEAR_PRECISION = 9
NOT_WIKIPEDIAS = {"commonswiki", "specieswiki", "metawiki", "wikidatawiki", "mediawikiwiki", "sourceswiki"}


@dataclass(frozen=True)
class Year:
    value: int
    precision: int
    circa: bool

    @property
    def approximate(self) -> bool:
        return self.circa or self.precision < YEAR_PRECISION

    @property
    def span(self) -> int:
        """Years covered: 1 for a year, 10 for a decade, 100 for a century, 1000 for a millennium."""
        return 10 ** max(YEAR_PRECISION - self.precision, 0)

    @property
    def earliest(self) -> int:
        """Wikidata stores the 1180s as 1180 and the 190s BC as -190, but the 3rd century as 300 and the 6th
        century BC as -600."""
        if self.span == 1:
            return self.value
        if self.span == 10:
            return self.value if self.value > 0 else self.value - 9
        return self.value - self.span + 1 if self.value > 0 else self.value

    @property
    def latest(self) -> int:
        return self.earliest + self.span - 1

    def as_span(self) -> Span:
        return Span(self.earliest, self.latest, self.circa)


class Entity:
    def __init__(self, data: dict):
        self.data = data
        self.id: str = data["id"]

    @property
    def label(self) -> str | None:
        return self.data.get("labels", {}).get("en", {}).get("value")

    @property
    def description(self) -> str | None:
        return self.data.get("descriptions", {}).get("en", {}).get("value")

    @property
    def enwiki(self) -> str | None:
        return self.data.get("sitelinks", {}).get("enwiki", {}).get("title")

    def statements(self, prop: str, preferred_only: bool = True) -> list[dict]:
        """Non-deprecated statements, by default keeping only preferred ones when any exist."""
        found = [claim for claim in self.data.get("claims", {}).get(prop, []) if claim["rank"] != "deprecated"]
        preferred = [claim for claim in found if claim["rank"] == "preferred"] if preferred_only else []
        return [claim for claim in preferred or found if claim["mainsnak"].get("datavalue")]

    def ids(self, *props: str, preferred_only: bool = True) -> list[str]:
        return list(dict.fromkeys(
            claim["mainsnak"]["datavalue"]["value"]["id"]
            for prop in props
            for claim in self.statements(prop, preferred_only)
            if isinstance(claim["mainsnak"]["datavalue"]["value"], dict) and "id" in claim["mainsnak"]["datavalue"]["value"]
        ))

    def qualifier_ids(self, prop: str, qualifiers: set[str]) -> list[str]:
        """Items named by the given qualifiers on any of the property's statements."""
        return list(dict.fromkeys(
            qualifier["datavalue"]["value"]["id"]
            for claim in self.statements(prop, preferred_only=False)
            for name, values in claim.get("qualifiers", {}).items() if name in qualifiers
            for qualifier in values
            if isinstance(qualifier.get("datavalue", {}).get("value"), dict) and "id" in qualifier["datavalue"]["value"]
        ))

    def coordinates(self) -> tuple[float, float] | None:
        """Longitude and latitude of the item's first coordinate location (P625)."""
        for claim in self.statements("P625"):
            value = claim["mainsnak"]["datavalue"]["value"]
            return value["longitude"], value["latitude"]
        return None

    def years(self, prop: str) -> list[Year]:
        found = []
        for claim in self.statements(prop):
            value = claim["mainsnak"]["datavalue"]["value"]
            if not isinstance(value, dict) or "time" not in value:
                continue
            circumstances = [
                qualifier["datavalue"]["value"]["id"]
                for qualifier in claim.get("qualifiers", {}).get(SOURCING_CIRCUMSTANCES, [])
                if qualifier.get("datavalue")
            ]
            found.append(Year(parse_year(value["time"]), value["precision"], CIRCA in circumstances))
        return found


def qualifier_year(claim: dict, prop: str) -> int | None:
    for qualifier in claim.get("qualifiers", {}).get(prop, []):
        value = qualifier.get("datavalue", {}).get("value")
        if isinstance(value, dict) and "time" in value:
            return parse_year(value["time"])
    return None


def parse_year(time: str) -> int:
    sign = -1 if time.startswith("-") else 1
    return sign * int(time[1:].split("-", 1)[0])


class Wikidata:
    def __init__(self, http: Http):
        self.http = http

    def entities(self, ids: list[str]) -> dict[str, Entity]:
        unique = sorted({qid for qid in ids if qid})
        found: dict[str, Entity] = {}
        for start in range(0, len(unique), BATCH):
            batch = unique[start : start + BATCH]
            params = {
                "action": "wbgetentities",
                "ids": "|".join(batch),
                "props": "labels|descriptions|claims|sitelinks",
                "languages": "en",
                "sitefilter": "enwiki",
                "format": "json",
            }
            data = self.http.json(API, params, namespace="wikidata")
            for qid, entity in data.get("entities", {}).items():
                if "missing" not in entity:
                    found[qid] = Entity(entity)
        return found

    def language_editions(self, ids: list[str]) -> dict[str, int]:
        """How many Wikipedia language editions have an article on each item."""
        unique = sorted(set(ids))
        found: dict[str, int] = {}
        for start in range(0, len(unique), 50):
            params = {"action": "wbgetentities", "ids": "|".join(unique[start : start + 50]), "props": "sitelinks",
                      "format": "json"}
            for qid, entity in self.http.json(API, params, namespace="sitelinks").get("entities", {}).items():
                found[qid] = sum(1 for site in entity.get("sitelinks", {}) if site.endswith("wiki")
                                 and site not in NOT_WIKIPEDIAS)
        return found

    def subclass_of(self, classes: list[str], roots: set[str], depth: int = 12) -> set[str]:
        """The classes whose superclass chain reaches one of the roots."""
        matching = set()
        for start in classes:
            seen, frontier = {start}, [start]
            for _ in range(depth):
                if seen & roots or not frontier:
                    break
                parents = self.entities(frontier)
                frontier = [parent for qid in frontier if qid in parents
                            for parent in parents[qid].ids("P279", preferred_only=False) if parent not in seen]
                seen.update(frontier)
            if seen & roots:
                matching.add(start)
        return matching

    def query(self, sparql: str) -> list[dict]:
        data = self.http.json(SPARQL, {"query": sparql, "format": "json"}, namespace="sparql")
        return [{key: value["value"] for key, value in row.items()} for row in data["results"]["bindings"]]


def entity_id(uri: str) -> str:
    return uri.rsplit("/", 1)[-1]
