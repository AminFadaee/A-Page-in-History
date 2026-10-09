import re
from dataclasses import dataclass

from page_in_history.http import Http
from page_in_history.sources.wikipedia import API, Wikipedia

PAGE_PREFIXES = ("Vital articles/Level 4/People", "Vital articles/Level 5/People")
STATE_PAGE_PREFIXES = ("Vital articles/Level 3", "Vital articles/Level 4/History", "Vital articles/Level 4/Geography",
                       "Vital articles/Level 5/History", "Vital articles/Level 5/Geography")
PROJECT_NAMESPACE = 4
HEADING = re.compile(r"^(=+)\s*(.*?)\s*=+\s*$")
ENTRY = re.compile(r"^#\s*(?:\{\{Icon\|[^}]*\}\}\s*)*'*\[\[([^\]|#]+)")


META_API = "https://meta.wikimedia.org/w/api.php"
META_LISTS = {
    "List of articles every Wikipedia should have": "Meta 1000",
    "List of articles every Wikipedia should have/Expanded/People": "Meta 10000",
}
META_ENTRY = re.compile(r"^#\s*'*\[\[d:(Q\d+)")
META_COUNT = re.compile(r",\s*\d+$")
META_EXCLUDED = ("Actors", "Performing artists", "Entertainers", "Musicians", "Criminals", "Directors", "Businesspeople", "Sport",
                 "Journalists")
COMMENT = re.compile(r"<!--.*?-->")


@dataclass(frozen=True)
class MetaEntry:
    qid: str
    list: str
    section: str


@dataclass(frozen=True)
class Entry:
    title: str
    level: int
    section: str

    @property
    def top_section(self) -> str:
        return self.section.split(" > ")[0]


def page_titles(http: Http, prefix: str) -> list[str]:
    params = {"action": "query", "list": "allpages", "apprefix": prefix, "apnamespace": PROJECT_NAMESPACE,
              "aplimit": "max", "apfilterredir": "nonredirects", "format": "json"}
    pages = http.json(API, params, namespace="wikipedia")["query"]["allpages"]
    return [page["title"] for page in pages if not page["title"].endswith("/Candidates")]


def entries(wikitext: str, level: int, page_section: str) -> list[Entry]:
    found, headings = [], {}
    for line in wikitext.splitlines():
        if heading := HEADING.match(line):
            depth = len(heading.group(1))
            headings = {key: value for key, value in headings.items() if key < depth}
            headings[depth] = heading.group(2)
        elif entry := ENTRY.match(line):
            parts = [page_section] if page_section else []
            parts += [headings[key] for key in sorted(headings) if key > 1]
            found.append(Entry(entry.group(1).strip(), level, " > ".join(dict.fromkeys(parts))))
    return found


def people(http: Http, wikipedia: Wikipedia) -> list[Entry]:
    """Every person on the Level 4 and Level 5 lists, keeping the most selective level for each."""
    found: dict[str, Entry] = {}
    for prefix in PAGE_PREFIXES:
        level = int(prefix.split("Level ")[1][0])
        for title in page_titles(http, prefix):
            page_section = title.split("/People", 1)[1].strip("/")
            for entry in entries(wikipedia.page_wikitext(title), level, page_section):
                found.setdefault(entry.title, entry)
    return list(found.values())


def article_levels(http: Http, wikipedia: Wikipedia) -> dict[str, int]:
    """The most selective level of every article on the history and geography lists, where states are filed."""
    found: dict[str, int] = {}
    for prefix in STATE_PAGE_PREFIXES:
        level = int(prefix.split("Level ")[1][0])
        for title in page_titles(http, prefix):
            for entry in entries(wikipedia.page_wikitext(title), level, ""):
                found[entry.title] = min(level, found.get(entry.title, level))
    return found


def essential_people(http: Http) -> list[MetaEntry]:
    """Entries of Meta-Wiki's lists of articles every Wikipedia should have, the core 1,000 and the people of the
    expanded 10,000, which editors from many language communities maintain as Wikidata items. People in entertainment,
    sport, business and crime sections are left out, as on the Vital Articles lists; the core list is filtered to
    people later, by their dates of birth and death."""
    found: list[MetaEntry] = []
    for page, name in META_LISTS.items():
        params = {"action": "parse", "page": page, "prop": "wikitext", "format": "json", "formatversion": 2}
        headings: dict[int, str] = {}
        for line in http.json(META_API, params, namespace="meta")["parse"]["wikitext"].splitlines():
            line = COMMENT.sub("", line).strip()
            if heading := HEADING.match(line):
                depth = len(heading.group(1))
                headings = {key: value for key, value in headings.items() if key < depth}
                headings[depth] = META_COUNT.sub("", heading.group(2))
            elif entry := META_ENTRY.match(line):
                section = " > ".join(headings[key] for key in sorted(headings) if key > 2 and "{{" not in headings[key])
                if not section.startswith(META_EXCLUDED):
                    found.append(MetaEntry(entry.group(1), name, section))
    return found
