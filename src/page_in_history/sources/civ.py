import re
from dataclasses import dataclass

from page_in_history.http import Http

API = "https://civilization.fandom.com/api.php"
GAMES = range(1, 8)
PAGE_BATCH = 50
SEE_WIKIPEDIA = re.compile(r"\{\{\s*(?:SeeWikipedia|seewp)\s*(?:\|\s*([^|}]+?)\s*)?[|}]", re.IGNORECASE)
GAME_SUFFIX = re.compile(r"\s*\(Civ\d\)(?:/.*)?$")


@dataclass(frozen=True)
class Leader:
    title: str
    game: int


def leaders(http: Http) -> list[Leader]:
    """The leaders of Civilization I to VII as English Wikipedia titles, from the Civilization wiki. A leader's page
    names its Wikipedia article, or is named after it; Civilization II's pages only redirect to their civilization,
    so those leaders are looked up by name. Names that land on a disambiguation page drop out later, as non-people."""
    found = []
    for game in GAMES:
        titles = _members(http, f"Category:Leaders (Civ{game})")
        texts = _contents(http, titles)
        for title in titles:
            match = SEE_WIKIPEDIA.search(texts.get(title, ""))
            found.append(Leader(match.group(1) if match and match.group(1) else GAME_SUFFIX.sub("", title), game))
    return list(dict.fromkeys(found))


def _members(http: Http, category: str) -> list[str]:
    params = {"action": "query", "list": "categorymembers", "cmtitle": category, "cmnamespace": 0, "cmlimit": "max",
              "format": "json", "formatversion": 2}
    return [member["title"] for member in http.json(API, params, namespace="civwiki")["query"]["categorymembers"]
            if GAME_SUFFIX.search(member["title"]) and not member["title"].startswith("Leaders")]


def _contents(http: Http, titles: list[str]) -> dict[str, str]:
    found = {}
    for start in range(0, len(titles), PAGE_BATCH):
        params = {"action": "query", "titles": "|".join(titles[start : start + PAGE_BATCH]), "prop": "revisions",
                  "rvprop": "content", "rvslots": "main", "format": "json", "formatversion": 2}
        for page in http.json(API, params, namespace="civwiki")["query"]["pages"]:
            if page.get("revisions"):
                found[page["title"]] = page["revisions"][0]["slots"]["main"]["content"]
    return found
