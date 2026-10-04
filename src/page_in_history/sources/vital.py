import re
from dataclasses import dataclass

from page_in_history.http import Http
from page_in_history.sources.wikipedia import API, Wikipedia

PAGE_PREFIXES = ("Vital articles/Level 4/People", "Vital articles/Level 5/People")
PROJECT_NAMESPACE = 4
HEADING = re.compile(r"^(=+)\s*(.*?)\s*=+\s*$")
ENTRY = re.compile(r"^#\s*(?:\{\{Icon\|[^}]*\}\}\s*)*'*\[\[([^\]|#]+)")


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
