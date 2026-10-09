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
MIN_LEVEL_5_POPULARITY = 78
PHOTO_ERA = 1860
PHOTOGRAPH = "Q125191"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
MACHINE_DATE = re.compile(r"QS:P\d*,\+(\d{3,4})-")
HUMAN_YEAR = re.compile(r"\b(\d{3,4})\b")
NOT_A_PORTRAIT = re.compile(r"\bcoins?\b|coinage|\bdinars?\b|\baure(?:us|i)\b|siliqua|calligraph", re.IGNORECASE)
MIN_VOTES = 2
LEADING_CENTURY = re.compile(r"^(?:c\.\s*)?\d+(?:st|nd|rd|th)[- ]century(?:[- ](?:BCE?|AD|CE))?[- ]+", re.IGNORECASE)
DATES_IN_PARENTHESES = re.compile(r"\s*\([^()]*\d[^()]*\)")
TRAILING_YEAR = re.compile(r",\s*(?:c\.\s*)?\d{1,4}\s*(?:BCE?|AD|CE)?\s*$")
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
    lifetime: list[int]
    occupation: str
    birth_country: str
    civilizations: list[str]
    related: list[str]
    contribution: str
    image: str = ""
    image_credit: str = ""
    photo: bool = False
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


def role(description: str | None) -> str:
    """A short description without its dates: '4th-century Christian bishop and saint (270–343)' becomes
    'Christian bishop and saint'; the life years are shown separately."""
    text = LEADING_CENTURY.sub("", DATES_IN_PARENTHESES.sub("", description or "")).strip()
    text = TRAILING_YEAR.sub("", text).strip(" ,")
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
        headings = self.wikipedia.resolve(list({heading for *_, section in candidates for heading in section}))
        selected = {qid for qid, identity in self.catalog.by_qid.items() if identity.name in polity_names}
        candidates = [(title, person, span, self._polities(section, headings)) for title, person, span, section in candidates]
        candidates = [candidate for candidate in candidates if self._other_votes(*candidate[1:]) & selected]
        texts = self.wikipedia.wikitext([title for title, *_ in candidates])
        links = {title: lead_links(texts.get(title, "")) for title, *_ in candidates}
        linked = self.wikipedia.resolve([link for found in links.values() for link in found])
        figures = []
        for title, person, span, section_polities in candidates:
            lead_polities = self._polities(links[title], linked)
            civilizations, related, votes = self._civilizations(person, span, lead_polities, section_polities)
            if set(civilizations) & polity_names:
                figures.append((title, person, span, civilizations, related, votes))
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

    def _other_votes(self, person: Entity, span: tuple[int, int], section_polities: list[str]) -> set[str]:
        """Civilizations voted for without the article's opening links. Since a civilization needs two votes and the
        opening can add only one, people with none of these cannot qualify and their articles need not be read."""
        votes = [qid for qid in person.ids("P27", preferred_only=False) if qid in self.catalog.by_qid]
        votes += section_polities + self._birthplace(person)
        return {qid for qid in votes if self._active(qid, span)}

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
        for title, person, span, civilizations, related, votes in figures:
            extract, short_description = introductions.get(title, ("", None))
            paragraph = first_paragraph(extract)
            contribution = predicate(paragraph)
            image, credit, photo, photo_check = self._image(person)
            known = self.pantheon.get(person.id)
            assembled.append(Figure(
                id=person.id,
                slug=slugify(title),
                name=title,
                wikipedia=title,
                role=role(short_description or person.description),
                life=life_label(person.years("P569"), person.years("P570")),
                lifetime=list(span),
                occupation=known.occupation.capitalize() if known else "",
                birth_country=known.birth_country if known else "",
                civilizations=civilizations,
                related=related,
                contribution=contribution,
                image=image,
                image_credit=credit,
                photo=photo,
                checks={"civilization_votes": votes, "lead": paragraph, "photo": photo_check},
            ))
        return assembled

    def _image(self, person: Entity) -> tuple[str, str, bool, str]:
        """The person's main Wikidata image unless Commons files it under coins or calligraphy (other images can show
        someone else), its credit, and whether it is a photograph of the person: only for people who died in or after
        1860, and only when the file was captured during their lifetime or Commons classifies it as a photograph."""
        files = [claim["mainsnak"]["datavalue"]["value"] for claim in person.statements("P18")][:1]
        for file in files:
            params = {"action": "query", "titles": f"File:{file}", "prop": "imageinfo|categories", "iiprop": "extmetadata",
                      "clshow": "!hidden", "cllimit": "max", "format": "json", "formatversion": 2}
            page = self.http.json(COMMONS_API, params, namespace="commons")["query"]["pages"][0]
            if "missing" in page or any(NOT_A_PORTRAIT.search(category["title"]) for category in page.get("categories", [])):
                continue
            metadata = page.get("imageinfo", [{}])[0].get("extmetadata", {})
            artist = plain(metadata.get("Artist", {}).get("value", ""))
            licence = metadata.get("LicenseShortName", {}).get("value", "")
            credit = " · ".join(part for part in (artist, licence) if part)
            captured = plain(metadata.get("DateTimeOriginal", {}).get("value", ""))
            photo, check = self._photograph(person, page.get("pageid"), captured)
            return page["title"].removeprefix("File:"), credit, photo, check
        return "", "", False, "image is a coin or calligraphy" if files else "no image"

    def _photograph(self, person: Entity, page_id: int | None, captured: str) -> tuple[bool, str]:
        births, deaths = person.years("P569"), person.years("P570")
        if not deaths or deaths[0].value < PHOTO_ERA:
            return False, f"no death year in or after {PHOTO_ERA}"
        year = capture_year(captured)
        if year is not None and births and births[0].earliest <= year <= deaths[0].latest:
            return True, f"captured {captured} during their lifetime"
        if page_id:
            media = self.http.json(COMMONS_API, {"action": "wbgetentities", "ids": f"M{page_id}", "format": "json"},
                                   namespace="commons")
            statements = media.get("entities", {}).get(f"M{page_id}", {}).get("statements") or {}
            kinds = [claim["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for claim in statements.get("P31", [])]
            if PHOTOGRAPH in kinds:
                return True, "classified as a photograph on Commons"
        return False, f"capture date {captured!r} not within their lifetime and not classified as a photograph"


def capture_year(captured: str) -> int | None:
    """The year from a Commons capture date: its machine-readable form ('QS:P571,+1863-11-08…') when present,
    otherwise the first three- or four-digit number ('8 November 1863', 'circa 1890')."""
    match = MACHINE_DATE.search(captured) or HUMAN_YEAR.search(captured)
    return int(match.group(1)) if match else None


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()
