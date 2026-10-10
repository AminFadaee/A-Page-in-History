import logging
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from shapely.geometry import Point

from page_in_history.dates import period_label
from page_in_history.naming import slugify
from page_in_history.photos import PortraitFinder
from page_in_history.polities import PolityCatalog
from page_in_history.sources import vital
from page_in_history.sources.pantheon import Person
from page_in_history.sources.wikidata import Entity, Year, qualifier_year
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
MIN_VOTES = 2
MEMBERSHIP_SOURCES = ("office", "category", "citizenship", "vital_section")
ADULT_AGE = 20
HEAD_OFFICES = ("P1906", "P1313")
MEMBERSHIP_QUALIFIERS = {"P27", "P1001", "P945"}
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
    image_is_photo: bool = False
    photo: str = ""
    photo_credit: str = ""
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


@dataclass(frozen=True)
class Candidate:
    title: str
    person: Entity
    lifetime: tuple[int, int]
    section: list[str]
    lists: tuple[str, ...] = ()

    @property
    def adulthood(self) -> tuple[int, int]:
        start, end = self.lifetime
        return (start + ADULT_AGE, end) if start + ADULT_AGE <= end else self.lifetime


class FigureBuilder:
    def __init__(self, catalog: PolityCatalog, pantheon: dict[str, Person], http, portraits: PortraitFinder):
        self.catalog = catalog
        self.portraits = portraits
        self.cliopatria = catalog.cliopatria
        self.wikipedia = catalog.wikipedia
        self.wikidata = catalog.wikidata
        self.pantheon = pantheon
        self.http = http
        self.head_offices: dict[str, set[str]] = {}
        for state in catalog.by_qid:
            for office in catalog.entities[state].ids(*HEAD_OFFICES, preferred_only=False):
                self.head_offices.setdefault(office, set()).add(state)

    def build(self, polity_names: set[str], start: int, end: int) -> list[Figure]:
        candidates = [candidate for candidate in self._candidates(start, end)
                      if candidate.lifetime[1] >= start and candidate.lifetime[0] <= end]
        titles = [candidate.title for candidate in candidates]
        self.headings = self.wikipedia.resolve(list({heading for candidate in candidates for heading in candidate.section}))
        self.offices = self.wikidata.entities([qid for candidate in candidates
                                               for qid in candidate.person.ids("P39", preferred_only=False)])
        self.places = self.wikidata.entities([qid for candidate in candidates for qid in candidate.person.ids("P20")])
        self.categories = self._category_states(self.wikipedia.categories(titles))
        texts = self.wikipedia.wikitext(titles)
        links = {title: lead_links(texts.get(title, "")) for title in titles}
        self.linked = self.wikipedia.resolve([link for found in links.values() for link in found])
        figures = []
        for candidate in candidates:
            civilizations, related, votes = self._civilizations(candidate, links[candidate.title])
            if set(civilizations) & polity_names:
                figures.append((candidate, civilizations, related, votes))
        logger.info("%d figures link to the selected civilizations", len(figures))
        return self._assemble(figures)

    def _candidates(self, start: int, end: int) -> list[Candidate]:
        """People on English Wikipedia's Vital Articles (level 4, or level 5 with a high Pantheon score) or on Meta-Wiki's
        lists of articles every Wikipedia should have, which many language communities maintain together and which
        therefore weigh the world more evenly than an English list."""
        entries = [entry for entry in vital.people(self.http, self.wikipedia)
                   if not entry.top_section.startswith(EXCLUDED_SECTIONS)]
        pages = self.wikipedia.resolve([entry.title for entry in entries])
        listed: dict[str, dict] = {}
        for entry in entries:
            page = pages.get(entry.title)
            if page and page.qid:
                found = listed.setdefault(page.qid, {"title": page.title, "section": entry.section, "lists": [], "vital": entry})
                found["lists"].append(f"Vital {entry.level}")
        for entry in vital.essential_people(self.http):
            found = listed.setdefault(entry.qid, {"title": None, "section": entry.section, "lists": [], "vital": None})
            found["lists"].append(entry.list)
        people = self.wikidata.entities([qid for qid in listed if self._maybe_alive(qid, start, end)])
        candidates = []
        for qid, found in listed.items():
            person = people.get(qid)
            title = found["title"] or (person.enwiki if person else None)
            if not person or not title or self._excluded(person) or not self._notable(person, found["vital"]):
                continue
            span = lifetime(person.years("P569"), person.years("P570"))
            if span:
                candidates.append(Candidate(title, person, span, found["section"].split(" > "), tuple(dict.fromkeys(found["lists"]))))
        return candidates

    def _maybe_alive(self, qid: str, start: int, end: int) -> bool:
        """Skips fetching people whose Pantheon birth year puts them clearly outside the window."""
        found = self.pantheon.get(qid)
        if found is None or found.birth_year is None:
            return True
        return start - ASSUMED_LIFESPAN <= found.birth_year <= end

    def _notable(self, person: Entity, entry: vital.Entry | None) -> bool:
        """Everyone on Meta-Wiki's lists or on Vital Articles level 4; from level 5 only people Pantheon ranks as widely
        known."""
        if entry is None or entry.level == 4:
            return True
        found = self.pantheon.get(person.id)
        return found is not None and found.popularity is not None and found.popularity >= MIN_LEVEL_5_POPULARITY

    def _excluded(self, person: Entity) -> bool:
        found = self.pantheon.get(person.id)
        return found is not None and found.occupation in EXCLUDED_OCCUPATIONS

    def _active(self, qid: str, years: tuple[int, int]) -> bool:
        rows = self.cliopatria.rows(self.catalog.by_qid[qid].name)
        return bool(((rows.ToYear >= years[0]) & (rows.FromYear <= years[1])).any())

    def _years_within(self, qid: str, years: tuple[int, int]) -> int:
        rows = self.cliopatria.rows(self.catalog.by_qid[qid].name)
        return max(0, min(years[1], int(rows.ToYear.max())) - max(years[0], int(rows.FromYear.min())))

    def _polities(self, titles: list[str], pages: dict) -> list[str]:
        return [pages[title].qid for title in titles if title in pages and pages[title].qid in self.catalog.by_qid]

    def _category_states(self, categories: dict[str, list[str]]) -> dict[str, list[str]]:
        """Each article's states named by its 'people of a state' categories: those that Wikidata describes as
        containing citizens, subjects or office holders of the state, not categories about relations with it."""
        pages = self.wikipedia.resolve([title for titles in categories.values() for title in titles])
        items = self.wikidata.entities([page.qid for page in pages.values() if page.qid])
        states = {title: [qid for qid in items[page.qid].qualifier_ids("P4224", MEMBERSHIP_QUALIFIERS)
                          if qid in self.catalog.by_qid]
                  for title, page in pages.items() if page.qid in items}
        return {article: [qid for title in titles for qid in states.get(title, [])]
                for article, titles in categories.items()}

    def _civilizations(self, candidate: Candidate, links: list[str]):
        """A civilization counts when it existed during the person's adult life and at least two sources agree:
        offices held, 'people of' categories, the place of death and the birthplace on the Cliopatria map, links
        in the article's opening, Wikidata citizenship and the Vital Articles section. A civilization backed only by
        places and opening links, with no source saying the person belonged to it (an office, a category, citizenship
        or the Vital Articles section), counts only when no other civilization of theirs has more votes: otherwise a
        death under a foreign occupation, like Saddam Hussein's in Baghdad drawn as American, would place them there.
        Holding a state's own head-of-state or head-of-government office confirms that state alone. The best supported
        come first, and
        among equals the one the person lived in longest as an adult."""
        person, adulthood = candidate.person, candidate.adulthood
        sources = {
            "office": self._offices(candidate),
            "category": self.categories.get(candidate.title, []),
            "death": self._located(person, "P20"),
            "lead": self._polities(links, self.linked),
            "citizenship": [qid for qid in person.ids("P27", preferred_only=False) if qid in self.catalog.by_qid],
            "vital_section": self._polities(candidate.section, self.headings),
            "birthplace": self._birthplace(person),
        }
        sources = {source: list(dict.fromkeys(qid for qid in qids if self._active(qid, adulthood)))
                   for source, qids in sources.items()}
        votes = Counter(qid for qids in sources.values() for qid in qids)
        headed = self._headed(candidate)
        member = {qid for source in MEMBERSHIP_SOURCES for qid in sources[source]}
        best = max(votes.values(), default=0)
        confirmed = sorted((qid for qid in set(votes) | headed
                            if (votes[qid] >= MIN_VOTES and (qid in member or votes[qid] == best)) or qid in headed),
                           key=lambda qid: (-votes[qid], -self._years_within(qid, adulthood)))
        related = [qid for qid in sources["lead"] if qid not in confirmed]
        names = self.catalog.by_qid
        readable = {source: [names[qid].name for qid in qids] for source, qids in sources.items() if qids}
        return [names[qid].name for qid in confirmed], [names[qid].name for qid in related], readable

    def _terms(self, candidate: Candidate):
        """Each office the person held, with its term, or their adult life when Wikidata gives no dates."""
        for claim in candidate.person.statements("P39", preferred_only=False):
            start, end = qualifier_year(claim, "P580"), qualifier_year(claim, "P582")
            term = (start if start is not None else candidate.adulthood[0],
                    end if end is not None else start if start is not None else candidate.adulthood[1])
            yield claim["mainsnak"]["datavalue"]["value"]["id"], term

    def _offices(self, candidate: Candidate) -> list[str]:
        """States an office governs: its jurisdiction, or the state whose head-of-state or head-of-government office
        it is, as long as the state existed during the term."""
        found = []
        for qid, term in self._terms(candidate):
            office = self.offices.get(qid)
            states = {state for state in office.ids("P1001", preferred_only=False) if state in self.catalog.by_qid} \
                if office else set()
            found += [state for state in states | self.head_offices.get(qid, set()) if self._active(state, term)]
        return found

    def _headed(self, candidate: Candidate) -> set[str]:
        return {state for qid, term in self._terms(candidate)
                for state in self.head_offices.get(qid, ()) if self._active(state, term)}

    def _located(self, person: Entity, prop: str) -> list[str]:
        """The states whose Cliopatria territory held the place of death in the year of death."""
        deaths = person.years("P570")
        for qid in person.ids(prop):
            place = self.places.get(qid)
            location = place.coordinates() if place else None
            if location and deaths:
                return self._states_at(location, deaths[0].latest)
        return []

    def _birthplace(self, person: Entity) -> list[str]:
        found = self.pantheon.get(person.id)
        if not found or not found.birthplace or found.birth_year is None:
            return []
        return self._states_at(found.birthplace, found.birth_year)

    def _states_at(self, location: tuple[float, float], year: int) -> list[str]:
        rows = self.cliopatria.at(year)
        rows = rows[rows.geometry.contains(Point(location))]
        return [self.catalog.identities[name].qid for name in rows.Name if name in self.catalog.identities]

    def _assemble(self, figures: list) -> list[Figure]:
        introductions = self.wikipedia.introductions([candidate.title for candidate, *_ in figures])
        assembled = []
        for candidate, civilizations, related, votes in figures:
            title, person, span = candidate.title, candidate.person, candidate.lifetime
            extract, short_description = introductions.get(title, ("", None))
            paragraph = first_paragraph(extract)
            contribution = predicate(paragraph)
            image, credit, image_check = self.portraits.illustration(person)
            photo, photo_check = self.portraits.photograph(person)
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
                image_is_photo=bool(photo) and photo.file == image,
                photo=photo.file if photo else "",
                photo_credit=photo.credit if photo else "",
                checks={"civilization_votes": votes, "lead": paragraph, "image": image_check,
                        "photo": photo.check if photo else photo_check},
            ))
        return assembled
