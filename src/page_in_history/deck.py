import hashlib
import html
import logging
import pathlib
import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import genanki

from page_in_history.dates import Span, centuries, period_label

DECK_NAME = "Page in History"
CIVILIZATION_MODEL_ID = 1_812_604_221
FIGURE_MODEL_ID = 1_812_604_222
TAG_PREFIX = "PIH"
MEDIA_PREFIX = "pih"
TAG_UNSAFE = re.compile(r"[^\w-]+")
MAX_MODERN_COUNTRIES = 10
MAX_COUNTRY_TAGS = 3

logger = logging.getLogger(__name__)


class Subdeck(StrEnum):
    CIVILIZATIONS = "Civilizations"
    SUCCESSION = "Succession"
    PERIODS = "Periods"
    FIGURES = "Figures"
    PHOTOS = "Photos"

    @property
    def full_name(self) -> str:
        return f"{DECK_NAME}::{self.value}"

    @property
    def deck_id(self) -> int:
        return stable_id(self.full_name)


@dataclass(frozen=True)
class Template:
    name: str
    subdeck: Subdeck
    requires: tuple[str, ...]
    front: str
    back: str

    def as_genanki(self, script: str = "") -> dict:
        return {"name": self.name, "qfmt": self.front + script, "afmt": self.back + script}


class RoutedCard(genanki.Card):
    def __init__(self, ord: int, deck_id: int):
        super().__init__(ord)
        self.deck_id = deck_id

    def write_to_db(self, cursor, timestamp: float, deck_id, note_id, id_gen, due=0):
        super().write_to_db(cursor, timestamp, self.deck_id, note_id, id_gen, due)


class RoutedNote(genanki.Note):
    def __init__(self, routed_cards: list[RoutedCard], **kwargs):
        super().__init__(**kwargs)
        self.routed_cards = routed_cards

    @property
    def cards(self) -> list[RoutedCard]:
        return self.routed_cards


def stable_id(name: str) -> int:
    return int(hashlib.sha256(name.encode()).hexdigest()[:12], 16) % (1 << 40) + (1 << 30)


UNKNOWN_ENTITY = '<div class="entity unknown">?</div>'
UNKNOWN_VALUE = '<div class="value unknown">?</div>'


def conditional(field: str, content: str) -> str:
    return f"{{{{#{field}}}}}{content}{{{{/{field}}}}}"


def guarded(fields: tuple[str, ...], content: str) -> str:
    for field in reversed(fields):
        content = conditional(field, content)
    return content


def entity(content: str, answered: bool = False) -> str:
    return f'<div class="entity answer">{content}</div>' if answered else f'<div class="entity">{content}</div>'


def value(content: str, answered: bool = False) -> str:
    return f'<div class="value answer">{content}</div>' if answered else f'<div class="value">{content}</div>'


def face(entity_html: str, label: str, value_html: str) -> str:
    return f'{entity_html}<hr class="divider"><div class="label">{label}</div>{value_html}'


def info(*lines: tuple[str, str]) -> str:
    rows = "".join(conditional(field, f'<div class="line"><span class="key">{key}</span> {{{{{field}}}}}</div>')
                   for key, field in lines)
    return f'<div class="info">{rows}</div>'


CIVILIZATION_TITLE = '{{Name}}<div class="subtitle">{{Period}}</div>'
CIVILIZATION_INFO = info(("Capital", "Capital"), ("Notable rulers", "Rulers"), ("Before", "Before"),
                         ("After", "After"), ("Today", "ModernCountries"))
MAP_IMAGE = '<div class="image map">{{Map}}</div>'
MAP_THUMBNAIL = '<div class="thumbnail map">{{Map}}</div>'


def neighbour_template(side: str) -> Template:
    """Who ruled the civilization's home region right before or after it, e.g. 'Before it in Iraq'."""
    label = f"{side} it in {{{{{side}Region}}}}"
    return Template(side, Subdeck.SUCCESSION, (side,),
                    face(entity(CIVILIZATION_TITLE), label, UNKNOWN_VALUE),
                    face(entity(CIVILIZATION_TITLE), label, value(f"{{{{{side}}}}}", answered=True)) + MAP_THUMBNAIL)


CIVILIZATION_TEMPLATES = (
    Template("Map", Subdeck.CIVILIZATIONS, ("Map",),
             face(UNKNOWN_ENTITY, "Map", MAP_IMAGE),
             face(entity(CIVILIZATION_TITLE, answered=True), "Map", MAP_IMAGE) + CIVILIZATION_INFO),
    neighbour_template("Before"),
    neighbour_template("After"),
    Template("Period", Subdeck.PERIODS, ("AskPeriod",),
             face(entity("{{Name}}"), "When", UNKNOWN_VALUE),
             face(entity("{{Name}}"), "When", value("{{Period}}", answered=True)) + MAP_THUMBNAIL),
)

CIVILIZATION_FIELDS = ("Id", "Name", "Map", "Period", "AskPeriod", "Capital", "Before", "BeforeRegion", "After",
                       "AfterRegion", "Rulers", "ModernCountries")

FIGURE_TITLE = '{{Name}}<div class="subtitle">{{Life}}</div>'
PORTRAIT = conditional("Image", '<div class="portrait">{{Image}}<div class="credit">{{ImageKind}} · {{ImageCredit}}</div></div>')
PHOTO = '<div class="value image photo">{{Image}}</div>'
FIGURE_INFO = info(("Civilization", "Civilization"), ("Known for", "Contribution"), ("Related", "Related"))

FIGURE_TEMPLATES = (
    Template("Who", Subdeck.FIGURES, ("Role",),
             face(entity("{{Name}}"), "Who", UNKNOWN_VALUE),
             face(entity(FIGURE_TITLE), "Who", value("{{Role}}", answered=True)) + FIGURE_INFO + PORTRAIT
             + conditional("CivilizationMap", '<div class="thumbnail map">{{CivilizationMap}}</div>')),
    Template("Photo", Subdeck.PHOTOS, ("Photo", "Image"),
             face(UNKNOWN_ENTITY, "Photo", PHOTO),
             face(entity(FIGURE_TITLE, answered=True), "Photo", PHOTO) + FIGURE_INFO),
)

FIGURE_FIELDS = ("Id", "Name", "Role", "Life", "Civilization", "CivilizationMap", "Contribution", "Image", "ImageCredit",
                 "ImageKind", "Photo", "Related")

CSS = """
.card {
  --text: #1d1d1f; --muted: #8a8a8e; --faint: #c2c2c8; --accent: #0a66c2;
  --line: #d8d8dd; --info: #505055; --background: #fdfdfd;
  font-family: -apple-system, "Segoe UI", Roboto, sans-serif; text-align: center;
  color: var(--text); background: var(--background); padding: 12px 8px;
}
.nightMode.card, .night_mode .card, .card.night_mode {
  --text: #e8e8ea; --muted: #9a9aa0; --faint: #5c5c62; --accent: #6cb2ff;
  --line: #3a3a3f; --info: #bdbdc2; --background: #1e1e20;
}
.entity { font-size: 30px; font-weight: 600; }
.subtitle { font-size: 18px; font-weight: 400; color: var(--muted); margin-top: 2px; }
.divider { border: none; border-top: 1px solid var(--line); width: min(60%, 360px); margin: 14px auto; }
.label { font-size: 14px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; }
.value { font-size: 24px; line-height: 1.35; max-width: 32em; margin: 0 auto; }
.unknown { color: var(--faint); font-weight: 600; }
.answer { color: var(--accent); font-weight: 700; }
.image img { width: 100%; max-width: 900px; height: auto; max-height: 60vh; object-fit: contain; }
.thumbnail { margin-top: 14px; }
.thumbnail img { width: 100%; max-width: 360px; height: auto; }
.info { font-size: 16px; line-height: 1.5; color: var(--info); margin: 14px auto 0; max-width: 40em; text-align: left; }
.info .line { margin-top: 4px; }
.info .key { color: var(--muted); font-size: 13px; letter-spacing: 0.06em; text-transform: uppercase; margin-right: 0.4em; }
.portrait { margin-top: 18px; }
.portrait img { max-height: 260px; max-width: 100%; border-radius: 6px; }
.photo img { max-height: 55vh; max-width: 100%; border-radius: 6px; }
.credit { font-size: 12px; color: var(--muted); margin-top: 4px; }
"""


MAP_ZOOM_CSS = """
.map img { cursor: zoom-in; }
.zoom-overlay { position: fixed; inset: 0; z-index: 1000; overflow: auto; background: rgba(0, 0, 0, .88); cursor: grab;
  -webkit-overflow-scrolling: touch; }
.zoom-overlay.dragging { cursor: grabbing; }
.zoom-stage { display: flex; min-width: 100%; min-height: 100%; width: max-content; height: max-content; }
.zoom-stage img { margin: auto; max-width: none; max-height: none; }
"""

MAP_ZOOM_SCRIPT = """
<script>
(function () {
  var ZOOM = 1.25;
  var root = document.getElementById("qa") || document.body;

  function open(image, event) {
    var bounds = image.getBoundingClientRect();
    var focusX = (event.clientX - bounds.left) / bounds.width;
    var focusY = (event.clientY - bounds.top) / bounds.height;
    var fit = Math.min(window.innerWidth / image.naturalWidth, window.innerHeight / image.naturalHeight);
    var overlay = document.createElement("div");
    var stage = document.createElement("div");
    var large = document.createElement("img");
    overlay.className = "zoom-overlay";
    stage.className = "zoom-stage";
    large.src = image.src;
    large.style.width = image.naturalWidth * fit * ZOOM + "px";
    large.style.height = image.naturalHeight * fit * ZOOM + "px";
    stage.appendChild(large);
    overlay.appendChild(stage);
    root.appendChild(overlay);
    overlay.scrollLeft = large.offsetLeft + focusX * large.offsetWidth - overlay.clientWidth / 2;
    overlay.scrollTop = large.offsetTop + focusY * large.offsetHeight - overlay.clientHeight / 2;
    pannable(overlay);
  }

  function pannable(overlay) {
    var drag = null;
    overlay.addEventListener("pointerdown", function (event) {
      if (event.pointerType !== "mouse") return;
      drag = { x: event.clientX, y: event.clientY, left: overlay.scrollLeft, top: overlay.scrollTop, moved: false };
      overlay.classList.add("dragging");
    });
    overlay.addEventListener("pointermove", function (event) {
      if (!drag) return;
      var dx = event.clientX - drag.x;
      var dy = event.clientY - drag.y;
      drag.moved = drag.moved || Math.abs(dx) + Math.abs(dy) > 4;
      overlay.scrollLeft = drag.left - dx;
      overlay.scrollTop = drag.top - dy;
    });
    overlay.addEventListener("pointerup", function () {
      overlay.classList.remove("dragging");
      setTimeout(function () { drag = null; }, 0);
    });
    overlay.addEventListener("click", function (event) {
      event.stopPropagation();
      if (!(drag && drag.moved)) overlay.remove();
    });
  }

  root.querySelectorAll(".map img").forEach(function (image) {
    image.addEventListener("click", function (event) {
      event.stopPropagation();
      open(image, event);
    });
  });
})();
</script>
"""


def description() -> str:
    return f"""
<p><b>{DECK_NAME}</b>: civilizations on the map and the people who shaped them.</p>
<p>Each question type lives in its own subdeck. To skip a type, <b>suspend</b> its subdeck rather than deleting it,
because deleted cards come back when you import an update.</p>
<p>Borders come from Cliopatria (Seshat Global History Databank, CC BY 4.0); facts from Wikidata (CC0) and Wikipedia
(CC BY-SA); people lists from Wikipedia's Vital Articles and Pantheon (CC BY); modern borders from Natural Earth
(public domain); portraits from Wikimedia Commons under the licence credited on each card. Built {date.today().isoformat()}.</p>
"""


def century_tag(number: int) -> str:
    """'PIH::Century::BC::06th', 'PIH::Century::AD::03rd': padded so the tag browser sorts them in order."""
    suffix = "th" if 10 <= abs(number) % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(abs(number) % 10, "th")
    return tag("Century", "BC" if number < 0 else "AD", f"{abs(number):02d}{suffix}")


def civilization_tags(document: dict) -> list[str]:
    tags = [tag("Civilization", document["name"])]
    period = document["period"]
    if period["confirmed"]:
        tags += [century_tag(number) for number in centuries(period["start"]["earliest"], period["end"]["latest"])]
    countries = document["modern_countries"][:MAX_COUNTRY_TAGS]
    countries += [document[side].get("region", "") for side in ("before", "after")]
    return tags + [tag("Country", country) for country in countries if country]


def figure_tags(document: dict) -> list[str]:
    tags = [tag("Civilization", name) for name in document["civilizations"]]
    tags += [century_tag(number) for number in centuries(*document["lifetime"])]
    tags += [tag("Country", document["birth_country"])] if document["birth_country"] else []
    tags += [tag("Occupation", document["occupation"])] if document["occupation"] else []
    return tags


def tag(*parts: str) -> str:
    return "::".join([TAG_PREFIX, *(TAG_UNSAFE.sub("_", part).strip("_") for part in parts)])


def escaped(text: str | None) -> str:
    return html.escape(text or "")


def joined(items: list[str], separator: str = ", ") -> str:
    return separator.join(escaped(item) for item in items)


def countries(names: list[str]) -> str:
    shown = joined(names[:MAX_MODERN_COUNTRIES])
    hidden = len(names) - MAX_MODERN_COUNTRIES
    return f"{shown} and {hidden} more" if hidden > 0 else shown


class MediaLibrary:
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path):
        self.data_dir = data_dir
        self.build_dir = build_dir
        self.files: set[str] = set()
        self.missing: list[pathlib.Path] = []

    def image(self, relative: str) -> str:
        if not relative:
            return ""
        source = self.data_dir / relative
        if not source.exists():
            self.missing.append(source)
            return ""
        target = self.build_dir / f"{MEDIA_PREFIX}-{source.parent.name}-{source.name}"
        if not target.exists() or target.stat().st_mtime < source.stat().st_mtime:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        self.files.add(str(target))
        return f'<img src="{target.name}">'


class DeckBuilder:
    def __init__(self, data_dir: pathlib.Path, build_dir: pathlib.Path):
        self.media = MediaLibrary(data_dir, build_dir / "media")
        self.civilization_model = self._model(CIVILIZATION_MODEL_ID, f"{DECK_NAME} civilization",
                                              CIVILIZATION_FIELDS, CIVILIZATION_TEMPLATES)
        self.figure_model = self._model(FIGURE_MODEL_ID, f"{DECK_NAME} figure", FIGURE_FIELDS, FIGURE_TEMPLATES)
        self.root = genanki.Deck(stable_id(DECK_NAME), DECK_NAME, description())
        self.subdecks = [genanki.Deck(subdeck.deck_id, subdeck.full_name) for subdeck in Subdeck]
        self.notes = 0

    def add_civilization(self, document: dict) -> None:
        period = document["period"]
        values = {
            "Id": document["id"],
            "Name": escaped(document["name"]),
            "Map": self.media.image(document["map"]),
            "Period": escaped(period_label(Span(**period["start"]), Span(**period["end"]))) if period["confirmed"] else "",
            "AskPeriod": "yes" if period["confirmed"] else "",
            "Capital": joined([capital["name"] for capital in document["capitals"]]),
            "Before": escaped(document["before"].get("name")),
            "BeforeRegion": escaped(document["before"].get("region")),
            "After": escaped(document["after"].get("name")),
            "AfterRegion": escaped(document["after"].get("region")),
            "Rulers": joined([ruler["name"] for ruler in document["rulers"]]),
            "ModernCountries": countries(document["modern_countries"]),
        }
        self._add(self.civilization_model, CIVILIZATION_FIELDS, CIVILIZATION_TEMPLATES, values,
                  civilization_tags(document), document["id"])

    def add_figure(self, document: dict, maps: dict[str, str]) -> None:
        civilization = document["civilizations"][0] if document["civilizations"] else ""
        values = {
            "Id": document["id"],
            "Name": escaped(document["name"]),
            "Role": escaped(document["role"]),
            "Life": escaped(document["life"]),
            "Civilization": joined(document["civilizations"]),
            "CivilizationMap": self.media.image(maps.get(civilization, "")),
            "Contribution": escaped(document["contribution"]),
            "Image": self.media.image(document["image"]),
            "ImageCredit": escaped(document["image_credit"]),
            "ImageKind": "Photograph" if document["photo"] else "Depiction",
            "Photo": "yes" if document["photo"] else "",
            "Related": joined(document["related"]),
        }
        tags = figure_tags(document)
        self._add(self.figure_model, FIGURE_FIELDS, FIGURE_TEMPLATES, values, tags, document["id"])

    def write(self, output: pathlib.Path) -> None:
        if self.media.missing:
            raise FileNotFoundError(f"{len(self.media.missing)} media files are missing, e.g. {self.media.missing[0]}")
        output.parent.mkdir(parents=True, exist_ok=True)
        package = genanki.Package([self.root, *self.subdecks], media_files=sorted(self.media.files))
        package.write_to_file(str(output))
        logger.info("Wrote %d notes and %d media files to %s", self.notes, len(self.media.files), output)

    def _add(self, model, fields, templates, values, tags, identity: str) -> None:
        cards = [RoutedCard(ord, template.subdeck.deck_id) for ord, template in enumerate(templates)
                 if all(values[field] for field in template.requires)]
        if not cards:
            return
        note = RoutedNote(cards, model=model, fields=[values[name] for name in fields], tags=sorted(set(tags)),
                          guid=genanki.guid_for(identity))
        self.root.add_note(note)
        self.notes += 1

    @staticmethod
    def _model(model_id: int, name: str, fields: tuple[str, ...], templates: tuple[Template, ...]) -> genanki.Model:
        return genanki.Model(
            model_id,
            name,
            fields=[{"name": field} for field in fields],
            templates=[Template(t.name, t.subdeck, t.requires, guarded(t.requires, t.front), t.back)
                       .as_genanki(MAP_ZOOM_SCRIPT) for t in templates],
            css=CSS + MAP_ZOOM_CSS,
            sort_field_index=1,
        )


def start_year(document: dict) -> int:
    start = document["period"]["start"]
    return start["earliest"] if start else document["map_year"]


def build_deck(civilizations: list[dict], figures: list[dict], data_dir: pathlib.Path, build_dir: pathlib.Path,
               output: pathlib.Path) -> None:
    builder = DeckBuilder(data_dir, build_dir)
    for document in sorted(civilizations, key=start_year):
        builder.add_civilization(document)
    maps = {document["name"]: document["map"] for document in civilizations}
    for document in sorted(figures, key=lambda document: document["name"]):
        builder.add_figure(document, maps)
    builder.write(output)
