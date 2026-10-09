import re
from dataclasses import dataclass, field

import mwparserfromhell as mw
from mwparserfromhell.wikicode import Wikicode

from page_in_history.dates import Span, parse, parse_range
from page_in_history.http import Http

API = "https://en.wikipedia.org/w/api.php"
BATCH = 50
EXTRACT_BATCH = 20
INFOBOXES = ("infobox country", "infobox former country")
NOTE_TEMPLATES = ("efn", "ref", "sfn", "refn", "notetag", "cn", "citation needed", "r", "nbsp")
FLAG_TEMPLATES = ("flag", "flagcountry", "flagu", "flag country", "flagdeco")
NON_ARTICLE = ("file:", "image:", "category:", "wikt:", ":category:")
SECTION = re.compile(r"\n==[^=]")
DASH_TEMPLATES = ("snd", "ndash", "spaced ndash", "spnd", "sndash", "mdash", "spaced en dash", "en dash")
DISAMBIGUATOR = re.compile(r"\s*\([^)]*\)$")


@dataclass(frozen=True)
class Page:
    title: str
    qid: str | None


@dataclass
class Infobox:
    start: Span | None = None
    end: Span | None = None
    capital: list[str] = field(default_factory=list)
    predecessors: list[str] = field(default_factory=list)
    successors: list[str] = field(default_factory=list)
    leaders: list[str] = field(default_factory=list)


def display_name(title: str) -> str:
    return DISAMBIGUATOR.sub("", title)


def place_name(title: str) -> str:
    """'Nisa, Turkmenistan' -> 'Nisa': Wikipedia disambiguates places with a comma."""
    return display_name(title).split(",")[0]


class Wikipedia:
    def __init__(self, http: Http):
        self.http = http

    def resolve(self, titles: list[str]) -> dict[str, Page]:
        """Follow normalisation and redirects; map each requested title to its article and Wikidata item."""
        unique = sorted({title for title in titles if title and "|" not in title})
        found: dict[str, Page] = {}
        for start in range(0, len(unique), BATCH):
            batch = unique[start : start + BATCH]
            params = {"action": "query", "titles": "|".join(batch), "prop": "pageprops", "ppprop": "wikibase_item",
                      "redirects": 1, "format": "json", "formatversion": 2}
            query = self.http.json(API, params, namespace="wikipedia")["query"]
            normalized = {item["from"]: item["to"] for item in query.get("normalized", [])}
            redirects = {item["from"]: item["to"] for item in query.get("redirects", [])}
            pages = {page["title"]: page for page in query["pages"]}
            for title in batch:
                target = normalized.get(title, title)
                target = redirects.get(target, target)
                page = pages.get(target, {})
                if not page.get("missing"):
                    found[title] = Page(target, page.get("pageprops", {}).get("wikibase_item"))
        return found

    def wikitext(self, titles: list[str]) -> dict[str, str]:
        unique = sorted(set(titles))
        found: dict[str, str] = {}
        for start in range(0, len(unique), BATCH):
            params = {"action": "query", "titles": "|".join(unique[start : start + BATCH]), "prop": "revisions",
                      "rvprop": "content", "rvslots": "main", "format": "json", "formatversion": 2}
            for page in self.http.json(API, params, namespace="wikipedia")["query"]["pages"]:
                if "revisions" in page:
                    found[page["title"]] = page["revisions"][0]["slots"]["main"]["content"]
        return found

    def categories(self, titles: list[str]) -> dict[str, list[str]]:
        """The visible categories of each article, following continuation since one batch can exceed the limit."""
        unique = sorted(set(titles))
        found: dict[str, list[str]] = {}
        for start in range(0, len(unique), BATCH):
            params = {"action": "query", "titles": "|".join(unique[start : start + BATCH]), "prop": "categories",
                      "clshow": "!hidden", "cllimit": "max", "format": "json", "formatversion": 2}
            while True:
                data = self.http.json(API, params, namespace="wikipedia")
                for page in data["query"]["pages"]:
                    found.setdefault(page["title"], []).extend(category["title"] for category in page.get("categories", []))
                if "continue" not in data:
                    break
                params = {**params, **data["continue"]}
        return found

    def introductions(self, titles: list[str]) -> dict[str, tuple[str, str | None]]:
        """Plain-text opening section and the short description of each article."""
        unique = sorted(set(titles))
        found: dict[str, tuple[str, str | None]] = {}
        for start in range(0, len(unique), EXTRACT_BATCH):
            params = {"action": "query", "titles": "|".join(unique[start : start + EXTRACT_BATCH]),
                      "prop": "extracts|pageprops", "exintro": 1, "explaintext": 1, "format": "json", "formatversion": 2}
            for page in self.http.json(API, params, namespace="wikipedia")["query"]["pages"]:
                found[page["title"]] = (page.get("extract") or "", page.get("pageprops", {}).get("wikibase-shortdesc"))
        return found

    def page_wikitext(self, title: str) -> str:
        params = {"action": "parse", "page": title, "prop": "wikitext", "format": "json", "formatversion": 2}
        return self.http.json(API, params, namespace="wikipedia")["parse"]["wikitext"]


def without_notes(code: Wikicode) -> Wikicode:
    for tag in code.filter_tags():
        if str(tag.tag).lower() == "ref":
            remove(code, tag)
    for template in code.filter_templates(recursive=False):
        if str(template.name).strip().lower().startswith(NOTE_TEMPLATES):
            remove(code, template)
    return code


def remove(code: Wikicode, node) -> None:
    try:
        code.remove(node)
    except ValueError:
        pass


def link_targets(code: Wikicode | None) -> list[str]:
    if code is None:
        return []
    code = without_notes(code)
    targets = [str(link.title).split("#")[0].split("{{!}}")[0].strip() for link in code.filter_wikilinks()]
    for template in code.filter_templates():
        if str(template.name).strip().lower() in FLAG_TEMPLATES and template.params:
            targets.append(str(template.params[0].value).strip())
    return [target for target in dict.fromkeys(targets) if target and not target.lower().startswith(NON_ARTICLE)]


def lead_links(wikitext: str) -> list[str]:
    """Articles linked from the prose of the lead section, ignoring infoboxes and other templates."""
    lead = mw.parse(SECTION.split(wikitext, maxsplit=1)[0])
    for template in lead.filter_templates(recursive=False):
        remove(lead, template)
    return link_targets(lead)


def plain_text(code: Wikicode | None) -> str:
    """Infobox wikitext as readable text: dash templates become dashes, wrappers like {{nowrap|…}} or {{circa|…}}
    keep their content, notes and line breaks go."""
    if code is None:
        return ""
    code = without_notes(mw.parse(str(code)))
    for template in reversed(code.filter_templates()):
        name = str(template.name).strip().lower()
        positional = [param for param in template.params if not param.showkey]
        if name in DASH_TEMPLATES:
            replacement = " – "
        elif name in ("circa", "c.", "c"):
            replacement = f"c. {positional[0].value}" if positional else "c."
        else:
            replacement = str(positional[-1].value) if positional else ""
        try:
            code.replace(template, replacement)
        except ValueError:
            pass
    text = mw.parse(str(code)).strip_code()
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", "", text.splitlines()[0] if text.strip() else "")).strip()


def infobox_years(fields: dict[str, Wikicode]) -> tuple[Span | None, Span | None]:
    """year_start and year_end, falling back to the two ends of life_span."""
    start, end = parse(plain_text(fields.get("year_start"))), parse(plain_text(fields.get("year_end")))
    if start is None or end is None:
        span_start, span_end = parse_range(plain_text(fields.get("life_span")))
        start, end = start or span_start, end or span_end
    return start, end


def numbered(fields: dict[str, Wikicode], prefix: str) -> list[str]:
    """Numbered fields such as p1, p2: each holds a link, or the bare article title that the infobox links itself."""
    keys = sorted((key for key in fields if re.fullmatch(rf"{prefix}\d+", key)), key=lambda key: int(key[len(prefix):]))
    found: list[str] = []
    for key in keys:
        if str(fields[key]).strip():
            found.extend(link_targets(fields[key]) or bare_title(fields[key]))
    return list(dict.fromkeys(found))


def bare_title(value: Wikicode) -> list[str]:
    """'Kingdom of France (1791–92){{!}}Kingdom of France' names the article before the escaped pipe."""
    title = mw.parse(str(value).split("{{!}}")[0]).strip_code().strip()
    return [title] if title else []


def country_infobox(wikitext: str) -> Infobox | None:
    for template in mw.parse(wikitext).filter_templates(recursive=False):
        if str(template.name).strip().lower() in INFOBOXES:
            fields = {str(param.name).strip(): param.value for param in template.params}
            start, end = infobox_years(fields)
            return Infobox(
                start=start,
                end=end,
                capital=link_targets(fields.get("capital")),
                predecessors=numbered(fields, "p"),
                successors=numbered(fields, "s"),
                leaders=numbered(fields, "leader"),
            )
    return None
