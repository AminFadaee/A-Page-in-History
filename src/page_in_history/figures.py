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
from page_in_history.sources import civ, curated, vital
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
MIN_CURATED_POPULARITY = 70
CURATED_LISTS = ("Banknote", "National poll", "Pantheon")
MIN_VOTES = 2
MEMBERSHIP_SOURCES = ("office", "category", "citizenship", "vital_section")
SINGLE_VOTE_SOURCES = ("office", "category", "vital_section")
CIV_LEADER = "Civ leader"
CORE_LISTS = ("Vital 4", "Meta 1000", CIV_LEADER)
RULER = "Ruler"
MIN_PLACES = 3
PLACES_PER_ROOT_YEAR = 1.5
MAX_RULER_SHARE = 0.5
RULING_OCCUPATIONS = {"POLITICIAN", "NOBLEMAN", "MILITARY PERSONNEL"}
ADULT_AGE = 20
POST_WAR = 1945
LEGENDARY = "Q13002315"
HUMAN = "Q5"
SUCCESSION_STEPS = 3
MIN_PULLED_KM2 = 20_000
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
    extended: bool = False
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
    def core(self) -> bool:
        """On a list of essential articles: Vital Articles level 4 or Meta-Wiki's core 1,000."""
        return any(name in self.lists for name in CORE_LISTS)

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

    def build(self, polity_names: set[str], start: int, end: int, rulers: list[str] = ()) -> list[Figure]:
        candidates = [candidate for candidate in self._candidates(start, end, rulers)
                      if candidate.lifetime[1] >= start and candidate.lifetime[0] <= end]
        self.prepare(candidates)
        placed = [(candidate, *self.civilizations(candidate)) for candidate in candidates]
        self.pulled = self.pulled_in(placed, polity_names)
        polity_names = polity_names | set(self.pulled)
        figures = [entry for entry in placed if set(entry[1]) & polity_names]
        extended = self.beyond_cap([(candidate, civilizations) for candidate, civilizations, *_ in figures], polity_names)
        logger.info("%d figures link to the selected civilizations, %d of them beyond their civilization's share",
                    len(figures), len(extended))
        return self._assemble(figures, extended)

    def pulled_in(self, placed: list[tuple], polity_names: set[str]) -> list[str]:
        """Civilizations outside the selection that a person on a core list needs, when none of the civilizations
        confirmed for them is selected: the best supported one that a source names them a member of (an office, a
        category, citizenship or the Vital Articles section), that began before 1945 like the deck's figures, and that
        reached 20,000 km², so a co-prince of Andorra or a free city does not bring in a micro-state. The core lists
        are short and weigh the world evenly, so they bring in states the selection passes over, such as the Crown of
        Castile for Isabella I or Lu for Confucius, without opening it to every small state."""
        pulled = set()
        for candidate, civilizations, _, votes in placed:
            if not candidate.core or set(civilizations) & polity_names:
                continue
            members = {name for source in MEMBERSHIP_SOURCES for name in votes.get(source, [])}
            joining = next((name for name in civilizations if name in members and self._can_join(name)), None)
            if joining:
                pulled.add(joining)
        return sorted(pulled)

    def _can_join(self, name: str) -> bool:
        qid = self.catalog.identities[name].qid
        if qid in self.catalog.mapless:
            return False
        rows = self.cliopatria.rows(name)
        return self.catalog.years(qid)[0] < POST_WAR and rows.Area.max() >= MIN_PULLED_KM2

    def beyond_cap(self, placed: list[tuple[Candidate, list[str]]], polity_names: set[str]) -> set[str]:
        """The figures past their civilization's share, which stay in the deck tagged as extended. Each civilization
        gets 1.5 times the square root of the years it lasted, at least 3, so long-lived ones keep more people than
        short-lived ones without the modern era crowding out the rest. People on the core lists come first, then those
        named by more lists, then by Pantheon's popularity, which corrects for how long ago they lived; rulers,
        politicians and holders of the civilization's offices take at most half the places while others are left."""
        by_civilization: dict[str, list[Candidate]] = {}
        for candidate, civilizations in placed:
            home = next(name for name in civilizations if name in polity_names)
            by_civilization.setdefault(home, []).append(candidate)
        extended = set()
        for name, people in by_civilization.items():
            first, last = self.catalog.years(self.catalog.identities[name].qid)
            places = max(MIN_PLACES, math.ceil(PLACES_PER_ROOT_YEAR * math.sqrt(max(last - first, 1))))
            ranked = sorted(people, key=lambda candidate: (not candidate.core, -len(set(candidate.lists)),
                                                           -self._popularity(candidate.person)))
            rulers_allowed, kept = math.ceil(places * MAX_RULER_SHARE), []
            others = [candidate for candidate in ranked if not self._rules(candidate, name)]
            for candidate in ranked:
                if len(kept) == places:
                    break
                ruling = self._rules(candidate, name)
                if ruling and rulers_allowed == 0 and any(other not in kept for other in others):
                    continue
                kept.append(candidate)
                rulers_allowed -= ruling
            extended |= {candidate.person.id for candidate in people if candidate not in kept}
        return extended

    def _popularity(self, person: Entity) -> float:
        known = self.pantheon.get(person.id)
        return known.popularity if known and known.popularity is not None else 0

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

    def _candidates(self, start: int, end: int, rulers: list[str] = ()) -> list[Candidate]:
        """People on English Wikipedia's Vital Articles (level 4, or level 5 with a high Pantheon score) or on Meta-Wiki's
        lists of articles every Wikipedia should have, which many language communities maintain together and which
        therefore weigh the world more evenly than an English list; the leaders of the Civilization games, a small set
        chosen to spread across the world's peoples and eras; the people nations put on their banknotes, vote their
        greatest or lay in their pantheons; and the notable rulers of the civilizations, if Pantheon
        ranks them as widely known. Left out: people whose adult life began after the Second World War, since the deck
        is about history, and figures Wikidata files as legendary and not as human, such as Moses (Jesus is filed as
        both)."""
        entries = [entry for entry in vital.people(self.http, self.wikipedia)
                   if not entry.top_section.startswith(EXCLUDED_SECTIONS)]
        pages = self.wikipedia.resolve([entry.title for entry in entries])
        listed: dict[str, dict] = {}
        for entry in entries:
            page = pages.get(entry.title)
            if page and page.qid:
                found = listed.setdefault(page.qid, {"title": page.title, "section": entry.section, "lists": []})
                found["lists"].append(f"Vital {entry.level}")
        for entry in vital.essential_people(self.http):
            found = listed.setdefault(entry.qid, {"title": None, "section": entry.section, "lists": []})
            found["lists"].append(entry.list)
        leaders = civ.leaders(self.http)
        leader_pages = self.wikipedia.resolve([leader.title for leader in leaders])
        for leader in leaders:
            page = leader_pages.get(leader.title)
            if page and page.qid:
                found = listed.setdefault(page.qid, {"title": page.title, "section": "", "lists": []})
                found["lists"].append(CIV_LEADER)
        named = curated.people(self.wikipedia)
        named_pages = self.wikipedia.resolve([title for titles in named.values() for title in titles])
        for name, titles in named.items():
            for title in titles:
                page = named_pages.get(title)
                if page and page.qid:
                    listed.setdefault(page.qid, {"title": page.title, "section": "", "lists": []})["lists"].append(name)
        for qid in rulers:
            listed.setdefault(qid, {"title": None, "section": "", "lists": []})["lists"].append(RULER)
        people = self.wikidata.entities([qid for qid in listed if self._maybe_alive(qid, start, end)])
        legendary = {qid for qid, kinds in self.wikidata.kinds(list(people), {LEGENDARY: "legendary"}).items()
                     if kinds and HUMAN not in people[qid].ids("P31", preferred_only=False)}
        candidates = []
        for qid, found in listed.items():
            person = people.get(qid)
            title = found["title"] or (person.enwiki if person else None)
            if not person or not title or qid in legendary or self._excluded(person) or not self._notable(person, found):
                continue
            span = lifetime(person.years("P569"), person.years("P570"))
            if span and span[0] + ADULT_AGE < POST_WAR:
                candidates.append(Candidate(title, person, span, found["section"].split(" > "), tuple(dict.fromkeys(found["lists"]))))
        return candidates

    def _maybe_alive(self, qid: str, start: int, end: int) -> bool:
        """Skips fetching people whose Pantheon birth year puts them clearly outside the window."""
        found = self.pantheon.get(qid)
        if found is None or found.birth_year is None:
            return True
        return start - ASSUMED_LIFESPAN <= found.birth_year <= end

    def _notable(self, person: Entity, found: dict) -> bool:
        """Everyone on a core list: Vital Articles level 4, Meta-Wiki's core 1,000 or the Civilization leaders. Others
        need Pantheon to rank them as widely known, or a little less so when at least two lists name them and one is
        curated by a nation for itself (its banknotes, its greatest-person poll, its pantheon), such as Ismail Samani,
        who is on Tajikistan's banknotes and the Vital Articles."""
        lists = set(found["lists"])
        if lists & set(CORE_LISTS):
            return True
        known = self.pantheon.get(person.id)
        popularity = known.popularity if known and known.popularity is not None else 0
        return popularity >= MIN_LEVEL_5_POPULARITY or (
            popularity >= MIN_CURATED_POPULARITY and len(lists) >= 2 and bool(lists & set(CURATED_LISTS)))

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
        come first, and
        among equals the one the person lived in longest as an adult."""
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
        confirmed = sorted((qid for qid in set(votes) | headed
                            if (votes[qid] >= MIN_VOTES and (qid in member or votes[qid] == best)) or qid in headed),
                           key=lambda qid: (-votes[qid], -self._years_within(qid, adulthood)))
        if not confirmed and candidate.core:
            confirmed = sorted({qid for source in SINGLE_VOTE_SOURCES for qid in sources[source]},
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

    def _assemble(self, figures: list, extended: set[str]) -> list[Figure]:
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
                extended=person.id in extended,
                checks={"civilization_votes": votes, "lead": paragraph, "image": image_check,
                        "photo": photo.check if photo else photo_check},
            ))
        return assembled
