import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from shapely.geometry import Point

from page_in_history.dates import period_label
from page_in_history.naming import slugify
from page_in_history.polities import PolityCatalog
from page_in_history.sources import vital
from page_in_history.sources.pantheon import Person
from page_in_history.sources.wikidata import Entity, Year
from page_in_history.sources.wikipedia import lead_links

EXCLUDED_SECTIONS = ("Entertainers", "Sports figures", "Directors, producers", "Musicians", "Businesspeople",
                     "Journalists", "Criminals")
EXCLUDED_OCCUPATIONS = {
    "SOCCER PLAYER", "ACTOR", "ATHLETE", "SINGER", "MUSICIAN", "FILM DIRECTOR", "BASKETBALL PLAYER", "CYCLIST",
    "TENNIS PLAYER", "SWIMMER", "WRESTLER", "RACING DRIVER", "SKIER", "HOCKEY PLAYER", "BOXER", "GYMNAST",
    "HANDBALL PLAYER", "SKATER", "COACH", "CHESS PLAYER", "FENCER", "MODEL", "CELEBRITY", "VOLLEYBALL PLAYER",
    "BADMINTON PLAYER", "PORNOGRAPHIC ACTOR", "MARTIAL ARTS", "PRESENTER", "REFEREE", "RUGBY PLAYER", "PRODUCER",
    "CRICKETER", "TABLE TENNIS PLAYER", "DANCER", "BASEBALL PLAYER", "COMEDIAN", "GOLFER", "GAME DESIGNER", "SNOOKER",
    "AMERICAN FOOTBALL PLAYER", "YOUTUBER", "POKER PLAYER", "MAGICIAN", "GAMER", "BULLFIGHTER", "GO PLAYER",
}
ASSUMED_LIFESPAN = 70
MIN_LEVEL_5_POPULARITY = 85
MIN_VOTES = 2
SHORT_DESCRIPTION_DATES = re.compile(r"\s*\((?:[^()]*\d[^()]*)\)\s*$|,?\s*(?:c\.\s*)?\d+(?:st|nd|rd|th)?[- ]century.*$")
COPULA = re.compile(r"\b(?:was|is|were)\s+", re.IGNORECASE)
PARENTHESES = re.compile(r"\s*\([^()]*\)")
SENTENCE_END = re.compile(r"(?<=[a-z0-9\])])\.\s+(?=[A-Z])")

logger = logging.getLogger(__name__)


@dataclass
class Figure:
    id: str
    slug: str
    name: str
    wikipedia: str
    role: str
    life: str
    civilizations: list[str]
    related: list[str]
    contribution: str
    image: str = ""
    image_credit: str = ""
    checks: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return asdict(self)


def lifetime(birth: list[Year], death: list[Year]) -> tuple[int, int] | None:
    if not birth and not death:
        return None
    start = min(year.earliest for year in birth) if birth else min(year.earliest for year in death) - ASSUMED_LIFESPAN
    end = max(year.latest for year in death) if death else max(year.latest for year in birth) + ASSUMED_LIFESPAN
    return start, end


def life_label(birth: list[Year], death: list[Year]) -> str:
    if not birth or not death:
        return ""
    return period_label(birth[0].as_span(), death[0].as_span())


def role(short_description: str | None) -> str:
    text = SHORT_DESCRIPTION_DATES.sub("", short_description or "").strip()
    return text[:1].upper() + text[1:]


def first_paragraph(extract: str) -> str:
    return next((line.strip() for line in extract.splitlines() if line.strip()), "")


def predicate(paragraph: str) -> str:
    """The opening sentence without its subject: 'The founder of the Achaemenid Empire.'"""
    sentence = SENTENCE_END.split(PARENTHESES.sub("", paragraph), maxsplit=1)[0].strip()
    parts = COPULA.split(sentence, maxsplit=1)
    if len(parts) < 2:
        return ""
    text = parts[1].strip().rstrip(".") + "."
    return text[:1].upper() + text[1:]


class FigureBuilder:
    def __init__(self, catalog: PolityCatalog, pantheon: dict[str, Person], http):
        self.catalog = catalog
        self.cliopatria = catalog.cliopatria
        self.wikipedia = catalog.wikipedia
        self.wikidata = catalog.wikidata
        self.pantheon = pantheon
        self.http = http

    def build(self, polity_names: set[str], start: int, end: int) -> list[Figure]:
        entries = [entry for entry in vital.people(self.http, self.wikipedia)
                   if not entry.top_section.startswith(EXCLUDED_SECTIONS)]
        pages = self.wikipedia.resolve([entry.title for entry in entries])
        people = self.wikidata.entities([page.qid for page in pages.values()
                                         if page.qid and self._maybe_alive(page.qid, start, end)])
        candidates = []
        for entry in entries:
            page = pages.get(entry.title)
            person = people.get(page.qid) if page and page.qid else None
            if not person or self._excluded(person) or not self._notable(person, entry):
                continue
            span = lifetime(person.years("P569"), person.years("P570"))
            if span and span[1] >= start and span[0] <= end:
                candidates.append((page.title, person, span, entry.section.split(" > ")))
        texts = self.wikipedia.wikitext([title for title, *_ in candidates])
        links = {title: lead_links(texts.get(title, "")) for title, *_ in candidates}
        headings = {heading for *_, section in candidates for heading in section}
        linked = self.wikipedia.resolve([link for found in links.values() for link in found] + list(headings))
        figures = []
        for title, person, span, section in candidates:
            lead_polities = self._polities(links[title], linked)
            section_polities = self._polities(section, linked)
            civilizations, related, votes = self._civilizations(person, span, lead_polities, section_polities)
            if set(civilizations) & polity_names:
                figures.append((title, person, civilizations, related, votes))
        logger.info("%d figures link to the selected civilizations", len(figures))
        return self._assemble(figures)

    def _maybe_alive(self, qid: str, start: int, end: int) -> bool:
        """Skips fetching people whose Pantheon birth year puts them clearly outside the window."""
        found = self.pantheon.get(qid)
        if found is None or found.birth_year is None:
            return True
        return start - ASSUMED_LIFESPAN <= found.birth_year <= end

    def _notable(self, person: Entity, entry: vital.Entry) -> bool:
        """Everyone on Vital Articles level 4; from level 5 only people Pantheon ranks as widely known."""
        if entry.level == 4:
            return True
        found = self.pantheon.get(person.id)
        return found is not None and found.popularity is not None and found.popularity >= MIN_LEVEL_5_POPULARITY

    def _excluded(self, person: Entity) -> bool:
        found = self.pantheon.get(person.id)
        return found is not None and found.occupation in EXCLUDED_OCCUPATIONS

    def _active(self, qid: str, span: tuple[int, int]) -> bool:
        rows = self.cliopatria.rows(self.catalog.by_qid[qid].name)
        return bool(((rows.ToYear >= span[0]) & (rows.FromYear <= span[1])).any())

    def _polities(self, titles: list[str], pages: dict) -> list[str]:
        return [pages[title].qid for title in titles if title in pages and pages[title].qid in self.catalog.by_qid]

    def _civilizations(self, person: Entity, span: tuple[int, int], lead_polities: list[str],
                       section_polities: list[str]):
        """A civilization counts when at least two sources agree: Wikidata citizenship, a link in the article's
        opening, the Vital Articles section the person is filed under, or the birthplace on the Cliopatria map."""
        sources = {
            "wikidata": [qid for qid in person.ids("P27", preferred_only=False) if qid in self.catalog.by_qid],
            "lead": lead_polities,
            "vital_section": section_polities,
            "birthplace": self._birthplace(person),
        }
        sources = {source: list(dict.fromkeys(qid for qid in qids if self._active(qid, span)))
                   for source, qids in sources.items()}
        votes = Counter(qid for qids in sources.values() for qid in qids)
        confirmed = [qid for qid, count in votes.most_common() if count >= MIN_VOTES]
        related = [qid for qid in sources["lead"] if qid not in confirmed]
        names = self.catalog.by_qid
        readable = {source: [names[qid].name for qid in qids] for source, qids in sources.items()}
        return [names[qid].name for qid in confirmed], [names[qid].name for qid in related], readable

    def _birthplace(self, person: Entity) -> list[str]:
        found = self.pantheon.get(person.id)
        if not found or not found.birthplace or found.birth_year is None:
            return []
        rows = self.cliopatria.at(found.birth_year)
        rows = rows[rows.geometry.contains(Point(found.birthplace))]
        return [self.catalog.identities[name].qid for name in rows.Name if name in self.catalog.identities]

    def _assemble(self, figures: list) -> list[Figure]:
        titles = [title for title, *_ in figures]
        introductions = self.wikipedia.introductions(titles)
        assembled = []
        for title, person, civilizations, related, votes in figures:
            extract, short_description = introductions.get(title, ("", None))
            paragraph = first_paragraph(extract)
            contribution = predicate(paragraph)
            image, credit = self._image(person)
            assembled.append(Figure(
                id=person.id,
                slug=slugify(title),
                name=title,
                wikipedia=title,
                role=role(short_description),
                life=life_label(person.years("P569"), person.years("P570")),
                civilizations=civilizations,
                related=related,
                contribution=contribution,
                image=image,
                image_credit=credit,
                checks={"civilization_votes": votes, "lead": paragraph},
            ))
        return assembled

    def _image(self, person: Entity) -> tuple[str, str]:
        files = [claim["mainsnak"]["datavalue"]["value"] for claim in person.statements("P18")]
        if not files:
            return "", ""
        return files[0], self._credit(files[0])

    def _credit(self, file: str) -> str:
        params = {"action": "query", "titles": f"File:{file}", "prop": "imageinfo", "iiprop": "extmetadata",
                  "format": "json", "formatversion": 2}
        pages = self.http.json("https://commons.wikimedia.org/w/api.php", params, namespace="commons")["query"]["pages"]
        metadata = pages[0].get("imageinfo", [{}])[0].get("extmetadata", {})
        artist = re.sub(r"<[^>]+>", "", metadata.get("Artist", {}).get("value", "")).strip()
        licence = metadata.get("LicenseShortName", {}).get("value", "")
        return " · ".join(part for part in (artist, licence) if part)
