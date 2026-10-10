import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, field

from shapely.geometry import Point

from page_in_history.dates import period_label
from page_in_history.naming import slugify
from page_in_history.photos import PortraitFinder
from page_in_history.polities import PolityCatalog
from page_in_history.sources import notable, vital
from page_in_history.sources.pantheon import Person
from page_in_history.sources.wikidata import Entity, Year, qualifier_year
from page_in_history.sources.wikipedia import lead_links

EXCLUDED_OCCUPATIONS = {
    "SOCCER PLAYER", "ACTOR", "ATHLETE", "SINGER", "MUSICIAN", "FILM DIRECTOR", "BASKETBALL PLAYER", "CYCLIST",
    "TENNIS PLAYER", "SWIMMER", "WRESTLER", "RACING DRIVER", "SKIER", "HOCKEY PLAYER", "BOXER", "GYMNAST",
    "HANDBALL PLAYER", "SKATER", "COACH", "CHESS PLAYER", "FENCER", "MODEL", "CELEBRITY", "VOLLEYBALL PLAYER",
    "BADMINTON PLAYER", "PORNOGRAPHIC ACTOR", "MARTIAL ARTS", "PRESENTER", "REFEREE", "RUGBY PLAYER", "PRODUCER",
    "CRICKETER", "TABLE TENNIS PLAYER", "DANCER", "BASEBALL PLAYER", "COMEDIAN", "GOLFER", "GAME DESIGNER", "SNOOKER",
    "AMERICAN FOOTBALL PLAYER", "YOUTUBER", "POKER PLAYER", "MAGICIAN", "GAMER", "BULLFIGHTER", "GO PLAYER",
}
ASSUMED_LIFESPAN = 70
MIN_VOTES = 2
MEMBERSHIP_SOURCES = ("office", "category", "citizenship", "vital_section")
SINGLE_VOTE_SOURCES = ("office", "category", "vital_section")
CORE_LIST = "Meta 1000"
PER_CELL = 60
STRONG_RANK, VERY_STRONG_RANK = 10, 3
WINDOW_YEARS = 300
MAX_PER_SEGMENT = 25
MAX_COMPOSERS = 20
RULING_OCCUPATIONS = {"POLITICIAN", "NOBLEMAN", "MILITARY PERSONNEL"}
COMPOSER = "COMPOSER"
ADULT_AGE = 20
POST_WAR = 1945
LEGENDARY = "Q13002315"
HUMAN = "Q5"
SUCCESSION_STEPS = 3
MIN_JOINING_KM2 = 20_000
LIVING_ON_MAP_BEFORE = 1900
DEATH_DISCOUNT = 0.5
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
    text = balanced(TRAILING_YEAR.sub("", text)).strip(" ,")
    return text[:1].upper() + text[1:]


def balanced(text: str) -> str:
    """The text without a closing parenthesis that has no opening one, or an opening one never closed, both left
    behind when dates are cut from a description."""
    depth, kept = 0, []
    for character in text:
        if character == ")" and depth == 0:
            continue
        depth += {"(": 1, ")": -1}.get(character, 0)
        kept.append(character)
    text = "".join(kept)
    return text[:text.rfind("(")].rstrip() if depth > 0 else text


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
    score: float
    cell_rank: int | None
    core: bool = False

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
        self.per_cell = PER_CELL
        self.head_offices: dict[str, set[str]] = {}
        for state in catalog.by_qid:
            for office in catalog.entities[state].ids(*HEAD_OFFICES, preferred_only=False):
                self.head_offices.setdefault(office, set()).add(state)

    def build(self, polity_names: set[str], start: int, end: int) -> list[Figure]:
        """Every candidate is placed against the whole catalog, so civilizations outside the selection can join
        through their people; then each civilization's figures are chosen era by era."""
        candidates = [candidate for candidate in self._candidates()
                      if candidate.lifetime[1] >= start and candidate.lifetime[0] <= end]
        self.prepare(candidates)
        placed = [(candidate, *self.civilizations(candidate)) for candidate in candidates]
        self.joined = self.people_backed(placed, polity_names)
        deck = polity_names | set(self.joined)
        figures = [entry for entry in placed if set(entry[1]) & deck]
        chosen = self.select([(candidate, civilizations) for candidate, civilizations, *_ in figures], deck)
        self.extended = [{"id": candidate.person.id, "name": candidate.title,
                          "civilization": next(name for name in civilizations if name in deck)}
                         for candidate, civilizations, *_ in figures if candidate.person.id not in chosen]
        logger.info("%d people placed in the deck's civilizations, %d chosen", len(figures), len(chosen))
        return self._assemble([entry for entry in figures if entry[0].person.id in chosen])

    def people_backed(self, placed: list[tuple], polity_names: set[str]) -> list[str]:
        """Civilizations outside the selection that their people make notable: two people placed there among the
        first 10 of their region and period, or one among the first 3, as Confucius is for Lu, or one on Meta-Wiki's
        list of 1,000 articles, as Laozi is for Chu. A civilization must
        have begun before 1900 and reached 20,000 km², so modern republics, a co-prince of Andorra or a free city do
        not bring in their state."""
        strong: Counter = Counter()
        very_strong: Counter = Counter()
        for candidate, civilizations, *_ in placed:
            if civilizations and civilizations[0] not in polity_names:
                rank = candidate.cell_rank or PER_CELL + 1
                strong[civilizations[0]] += rank <= STRONG_RANK
                very_strong[civilizations[0]] += rank <= VERY_STRONG_RANK or candidate.core
        return sorted(name for name in strong
                      if (strong[name] >= 2 or very_strong[name] >= 1) and self._can_join(name))

    def _can_join(self, name: str) -> bool:
        qid = self.catalog.identities[name].qid
        if qid in self.catalog.mapless:
            return False
        return self.catalog.years(qid)[0] < LIVING_ON_MAP_BEFORE and self.cliopatria.rows(name).Area.max() >= MIN_JOINING_KM2

    def select(self, placed: list[tuple[Candidate, list[str]]], deck: set[str], per_cell: int = PER_CELL) -> set[str]:
        """The figures for the deck. Each figure belongs to its first civilization in the deck, and each civilization
        is split into eras of at most 300 years, so its people compete with their own contemporaries. In each such
        segment people are ranked by the database's visibility score; chosen are those among the first 60 of their
        region and period, the segment's best person always, and anyone on Meta-Wiki's list of 1,000 articles. A
        segment keeps at most 25, and rulers, politicians and holders of the civilization's offices at most as many as
        everyone else, so places are left empty rather than filled with minor kings. Composers are capped at 20 across
        the deck."""
        segments: dict[tuple[str, int], list[Candidate]] = {}
        for candidate, civilizations in placed:
            home = next(name for name in civilizations if name in deck)
            segments.setdefault((home, self._window(home, candidate)), []).append(candidate)
        chosen: list[Candidate] = []
        for (home, _), people in segments.items():
            ranked = sorted(people, key=lambda candidate: -candidate.score)
            wanted = [candidate for index, candidate in enumerate(ranked)
                      if index == 0 or candidate.core or (candidate.cell_rank and candidate.cell_rank <= per_cell)]
            rulers = [candidate for candidate in wanted if self._rules(candidate, home)]
            others = [candidate for candidate in wanted if candidate not in rulers]
            kept_rulers = rulers[:max(1, len(others))] if rulers else []
            kept = sorted(others + kept_rulers, key=lambda candidate: -candidate.score)[:MAX_PER_SEGMENT]
            chosen += kept + [candidate for candidate in wanted if candidate.core and candidate not in kept]
        composers = sorted((candidate for candidate in chosen if self._occupation(candidate) == COMPOSER),
                           key=lambda candidate: -candidate.score)
        dropped = {candidate.person.id for candidate in composers[MAX_COMPOSERS:]}
        return {candidate.person.id for candidate in chosen} - dropped

    def _window(self, home: str, candidate: Candidate) -> int:
        first, last = self.catalog.years(self.catalog.identities[home].qid)
        windows = max(1, math.ceil((last - first) / WINDOW_YEARS))
        start = min(max(candidate.adulthood[0], first), last)
        return min(windows - 1, int((start - first) * windows / max(last - first + 1, 1)))

    def _occupation(self, candidate: Candidate) -> str:
        known = self.pantheon.get(candidate.person.id)
        return known.occupation if known else ""

    def _rules(self, candidate: Candidate, home: str) -> bool:
        """A ruler or politician by Pantheon's occupation, or anyone who held an office of their civilization, such as
        a pope of the Papal States, whom Pantheon files as a religious figure."""
        known = self.pantheon.get(candidate.person.id)
        return ((bool(known) and known.occupation in RULING_OCCUPATIONS)
                or self.catalog.identities[home].qid in self._offices(candidate))

    def prepare(self, candidates: list[Candidate]) -> None:
        """Fetches everything the votes need for these candidates, in batches."""
        titles = [candidate.title for candidate in candidates]
        self.headings = self.wikipedia.resolve(list({heading for candidate in candidates for heading in candidate.section}))
        self.offices = self.wikidata.entities([qid for candidate in candidates
                                               for qid in candidate.person.ids("P39", preferred_only=False)])
        self.places = self.wikidata.entities([qid for candidate in candidates for qid in candidate.person.ids("P20")])
        self.categories = self._category_states(self.wikipedia.categories(titles))
        texts = self.wikipedia.wikitext(titles)
        self.links = {title: lead_links(texts.get(title, "")) for title in titles}
        self.linked = self.wikipedia.resolve([link for found in self.links.values() for link in found])
        self._prepare_citizenships(candidates)

    def _prepare_citizenships(self, candidates: list[Candidate]) -> None:
        """Wikidata often gives citizenship as an umbrella civilization (Ancient Rome) or today's country (France)
        rather than a state Cliopatria draws. An umbrella counts for its parts, the states Wikidata lists as making it
        up; when none of them existed then, it counts like a country. A country counts for the Cliopatria state that
        held its capital, but only one Wikidata ties to the country, as an earlier form of it (Kingdom of Great Britain
        for the United Kingdom) or by naming it as its country; the holder of a capital is otherwise often an occupier,
        such as Japan in Seoul. Both are judged over the person's adult life."""
        others = {qid for candidate in candidates for qid in candidate.person.ids("P27", preferred_only=False)
                  if qid not in self.catalog.by_qid}
        items = self.wikidata.entities(list(others))
        part_of: dict[str, set[str]] = {}
        for state in self.catalog.by_qid:
            for whole in self.catalog.entities[state].ids("P361", preferred_only=False):
                part_of.setdefault(whole, set()).add(state)
        self.umbrellas = {qid: ({part for part in item.ids("P527", preferred_only=False) if part in self.catalog.by_qid}
                                | part_of.get(qid, set())) for qid, item in items.items()}
        capitals = self.wikidata.entities([capital for item in items.values() for capital in item.ids("P36")])
        frame = self.cliopatria.frame
        self.capital_holders: dict[str, list[tuple[str, int, int]]] = {}
        for qid, item in items.items():
            location = next((capitals[c].coordinates() for c in item.ids("P36") if c in capitals and capitals[c].coordinates()), None)
            if location:
                rows = frame[frame.geometry.contains(Point(location)) & frame.Name.isin(self.catalog.identities)]
                holders = [(self.catalog.identities[name].qid, int(first), int(last))
                           for name, first, last in zip(rows.Name, rows.FromYear, rows.ToYear)]
                linked = self._linked({holder for holder, *_ in holders}, qid)
                self.capital_holders[qid] = [holder for holder in holders if holder[0] in linked]

    def _linked(self, holders: set[str], country: str) -> set[str]:
        """The holders Wikidata ties to the country: through their country (P17), or a chain of up to three
        successions from the holder (replaced by, followed by) or back from the country (replaces, follows)."""
        linked = {holder for holder in holders if country in self.catalog.entities[holder].ids("P17", preferred_only=False)}
        forward = {holder: {holder} for holder in holders}
        backward = {country}
        for _ in range(SUCCESSION_STEPS):
            frontier = {qid for reached in forward.values() for qid in reached} | backward
            entities = self.wikidata.entities(list(frontier))
            backward |= {qid for step in backward if step in entities
                         for qid in entities[step].ids("P1365", "P155", preferred_only=False)}
            for holder, reached in forward.items():
                reached |= {qid for step in reached if step in entities
                            for qid in entities[step].ids("P1366", "P156", preferred_only=False)}
        return linked | {holder for holder, reached in forward.items() if country in reached or holder in backward}

    def _citizenships(self, person: Entity, adulthood: tuple[int, int]) -> list[str]:
        found = []
        for qid in person.ids("P27", preferred_only=False):
            if qid in self.catalog.by_qid:
                found.append(qid)
            elif parts := [part for part in self.umbrellas.get(qid, ()) if self._active(part, adulthood)]:
                found += parts
            elif self.capital_holders.get(qid):
                years: Counter = Counter()
                for state, first, last in self.capital_holders[qid]:
                    years[state] += max(0, min(last, adulthood[1]) - max(first, adulthood[0]) + 1)
                if years and years.most_common(1)[0][1] > 0:
                    found.append(years.most_common(1)[0][0])
        return found

    def _candidates(self) -> list[Candidate]:
        """The first people of every region and period in the cross-verified database of notable people, and
        everyone on Meta-Wiki's core list of 1,000 articles every Wikipedia should have, so no essential figure depends
        on the database alone. Left out: people whose adult life began after the Second World War, since the deck is
        about history; sport, business and entertainment; and figures Wikidata files as legendary and not as human,
        such as Moses (Jesus is filed as both)."""
        frame = notable.load(self.http)
        pool = notable.pool(frame, POST_WAR, ADULT_AGE, self.per_cell)
        core = {entry.qid for entry in vital.essential_people(self.http) if entry.list == CORE_LIST}
        qids = list(dict.fromkeys(list(pool.index) + sorted(core)))
        sections = self._vital_sections()
        people = self.wikidata.entities(qids)
        legendary = {qid for qid, kinds in self.wikidata.kinds(list(people), {LEGENDARY: "legendary"}).items()
                     if kinds and HUMAN not in people[qid].ids("P31", preferred_only=False)}
        candidates = []
        for qid in qids:
            person = people.get(qid)
            if not person or not person.enwiki or qid in legendary or self._excluded(person):
                continue
            span = lifetime(person.years("P569"), person.years("P570"))
            if span and span[0] + ADULT_AGE < POST_WAR:
                score = float(frame.score.get(qid, 0.0))
                cell_rank = int(pool.cell_rank[qid]) if qid in pool.index else None
                candidates.append(Candidate(person.enwiki, person, span, sections.get(qid, []), score, cell_rank, qid in core))
        return candidates

    def _vital_sections(self) -> dict[str, list[str]]:
        """The section each person is filed under on Wikipedia's Vital Articles lists, such as 'Rulers > Persia', a
        placement vote."""
        entries = vital.people(self.http, self.wikipedia)
        pages = self.wikipedia.resolve([entry.title for entry in entries])
        return {pages[entry.title].qid: entry.section.split(" > ") for entry in entries
                if entry.title in pages and pages[entry.title].qid}

    def _excluded(self, person: Entity) -> bool:
        found = self.pantheon.get(person.id)
        return found is not None and found.occupation in EXCLUDED_OCCUPATIONS

    def _active(self, qid: str, years: tuple[int, int]) -> bool:
        if qid in self.catalog.mapless:
            first, last = self.catalog.mapless[qid]
            return first <= years[1] and last >= years[0]
        rows = self.cliopatria.rows(self.catalog.by_qid[qid].name)
        return bool(((rows.ToYear >= years[0]) & (rows.FromYear <= years[1])).any())

    def _years_within(self, qid: str, years: tuple[int, int]) -> int:
        first, last = self.catalog.years(qid)
        return max(0, min(years[1], last) - max(years[0], first))

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

    def civilizations(self, candidate: Candidate):
        """A civilization counts when it existed during the person's adult life and at least two sources agree:
        offices held, 'people of' categories, the place of death and the birthplace on the Cliopatria map, links
        in the article's opening, Wikidata citizenship and the Vital Articles section. A civilization backed only by
        places and opening links, with no source saying the person belonged to it (an office, a category, citizenship
        or the Vital Articles section), counts only when no other civilization of theirs has more votes: otherwise a
        death under a foreign occupation, like Saddam Hussein's in Baghdad drawn as American, would place them there.
        Holding a state's own head-of-state or head-of-government office confirms that state alone. The best supported
        come first, a place of death counting half there since exiles die abroad, and among equals the one the person
        lived in longest as an adult."""
        person, adulthood = candidate.person, candidate.adulthood
        sources = {
            "office": self._offices(candidate),
            "category": self.categories.get(candidate.title, []),
            "death": self._located(person, "P20"),
            "lead": self._polities(self.links[candidate.title], self.linked),
            "citizenship": self._citizenships(person, adulthood),
            "vital_section": self._polities(candidate.section, self.headings),
            "birthplace": self._birthplace(person),
        }
        sources = {source: list(dict.fromkeys(qid for qid in qids if self._active(qid, adulthood)))
                   for source, qids in sources.items()}
        votes = Counter(qid for qids in sources.values() for qid in qids)
        headed = self._headed(candidate)
        member = {qid for source in MEMBERSHIP_SOURCES for qid in sources[source]}
        best = max(votes.values(), default=0)
        support = lambda qid: (-(votes[qid] - DEATH_DISCOUNT * (qid in sources["death"])),
                               -self._years_within(qid, adulthood))
        confirmed = sorted((qid for qid in set(votes) | headed
                            if (votes[qid] >= MIN_VOTES and (qid in member or votes[qid] == best)) or qid in headed),
                           key=support)
        if not confirmed and candidate.core:
            confirmed = sorted({qid for source in SINGLE_VOTE_SOURCES for qid in sources[source]}, key=support)
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
