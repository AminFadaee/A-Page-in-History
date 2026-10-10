import re

from page_in_history.sources.wikipedia import Wikipedia

BANKNOTES = "List of people on banknotes"
POLLS = "Greatest Britons spin-offs"
FIRST_POLL = "100 Greatest Britons"
PANTHEONS = ("Panthéon", "Walhalla (memorial)", "Burials and memorials in Westminster Abbey", "Santa Croce, Florence",
             "Pantheon of Illustrious Men", "National Pantheon of Venezuela")
LINK = re.compile(r"\[\[([^\]|#]+)")
POLL_SHOW = re.compile(r"'''''\[\[([^\]|]+)")
LIST_LINE = re.compile(r"^\s*[#*|!]")
NOT_ARTICLES = ("File:", "Image:", "Category:", "wikt:", ":")


def people(wikipedia: Wikipedia) -> dict[str, list[str]]:
    """Article titles named by lists that communities and institutions curate for their own nation: the people on
    each country's banknotes, the national polls modelled on 100 Greatest Britons, and the national pantheons and
    halls of fame. Lists link more than people (places, presenters), so the titles are checked to be people later."""
    found = {"Banknote": _banknotes(wikipedia.wikitext([BANKNOTES]).get(BANKNOTES, ""))}
    shows = [FIRST_POLL] + POLL_SHOW.findall(wikipedia.wikitext([POLLS]).get(POLLS, ""))
    found["National poll"] = [title for text in wikipedia.wikitext(shows).values() for title in _listed(text)]
    found["Pantheon"] = [title for text in wikipedia.wikitext(list(PANTHEONS)).values() for title in _listed(text)]
    return {name: list(dict.fromkeys(titles)) for name, titles in found.items()}


def _banknotes(text: str) -> list[str]:
    """The person heading each row of the per-country tables."""
    return [match.group(1).strip() for line in text.splitlines()
            if line.startswith("!") and (match := LINK.search(line))]


def _listed(text: str) -> list[str]:
    return [link.strip() for line in text.splitlines() if LIST_LINE.match(line)
            for link in LINK.findall(line) if not link.startswith(NOT_ARTICLES)]
