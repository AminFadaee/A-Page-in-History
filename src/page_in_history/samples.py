import json
import logging
import pathlib
import re
import shutil
import sqlite3
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass

from PIL import Image, ImageChops

CHROMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser")
WINDOW_SIZE = (960, 1800)
SCALE = 2
MARGIN_PX = 12
QUALITY = 85
FIELD_SEPARATOR = "\x1f"
SECTION = re.compile(r"\{\{([#^])([^}]+)\}\}(.*?)\{\{/\2\}\}", re.DOTALL)
FIELD = re.compile(r"\{\{([^#^/}][^}]*)\}\}")
PAGE_CSS = """
html, body { margin: 0; background: #fff; }
.pair { display: flex; gap: 16px; align-items: stretch; padding: 16px; }
.face { width: 420px; border: 1px solid #d9dfe5; border-radius: 14px; overflow: hidden; background: #fdfdfd; }
.side { font: 11px/1 -apple-system, "Segoe UI", Roboto, sans-serif; letter-spacing: .08em; text-transform: uppercase;
  color: #5b6876; padding: 10px 12px 0; }
.face .card { min-height: 300px; }
"""

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sample:
    note_type: str
    card: str
    note: str
    file: str


SAMPLES = (
    Sample("Page in History civilization", "Map", "Parthian Empire", "civilization-map.webp"),
    Sample("Page in History civilization", "Succession", "Sasanian Empire", "civilization-succession.webp"),
    Sample("Page in History civilization", "Succession", "Seleucid Empire", "civilization-succession-one-side.webp"),
    Sample("Page in History civilization", "Period", "Achaemenid Empire", "civilization-period.webp"),
    Sample("Page in History figure", "Who", "Cyrus the Great", "figure-who.webp"),
    Sample("Page in History figure", "Photo", "Albert Einstein", "figure-photo.webp"),
)


class SampleError(LookupError):
    pass


@dataclass(frozen=True)
class NoteType:
    fields: list[str]
    templates: dict[str, tuple[str, str]]
    css: str
    sort_field: int


class Package:
    def __init__(self, path: pathlib.Path, media_dir: pathlib.Path):
        with zipfile.ZipFile(path) as archive:
            with tempfile.TemporaryDirectory() as unpacked:
                archive.extract("collection.anki2", unpacked)
                database = sqlite3.connect(pathlib.Path(unpacked) / "collection.anki2")
                try:
                    models = json.loads(database.execute("SELECT models FROM col").fetchone()[0])
                    rows = database.execute("SELECT mid, flds FROM notes").fetchall()
                finally:
                    database.close()
            for index, name in json.loads(archive.read("media")).items():
                (media_dir / name).write_bytes(archive.read(index))
        self.note_types = {
            model["name"]: (str(model_id), NoteType(
                [field["name"] for field in model["flds"]],
                {template["name"]: (template["qfmt"], template["afmt"]) for template in model["tmpls"]},
                model["css"],
                model["sortf"],
            ))
            for model_id, model in models.items()
        }
        self.notes = [(str(model_id), fields.split(FIELD_SEPARATOR)) for model_id, fields in rows]

    def note(self, note_type: str, name: str) -> tuple[NoteType, dict[str, str]]:
        model_id, kind = self.note_types[note_type]
        for owner, values in self.notes:
            if owner == model_id and values[kind.sort_field] == name:
                return kind, dict(zip(kind.fields, values, strict=True))
        raise SampleError(f"No {note_type} note named {name}")


def render_template(template: str, fields: dict[str, str]) -> str:
    def section(match: re.Match) -> str:
        shown = bool(fields.get(match.group(2).strip(), "").strip())
        return match.group(3) if shown == (match.group(1) == "#") else ""

    previous = None
    while previous != template:
        previous, template = template, SECTION.sub(section, template)
    return FIELD.sub(lambda match: fields.get(match.group(1).strip(), ""), template)


def sample_page(front: str, back: str, css: str) -> str:
    faces = "".join(
        f'<div class="face"><div class="side">{side}</div><div class="card">{content}</div></div>'
        for side, content in (("Front", front), ("Back", back))
    )
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{css}{PAGE_CSS}</style></head>' \
           f'<body><div class="pair">{faces}</div></body></html>'


def find_chrome() -> str:
    for name in CHROMES:
        if path := shutil.which(name):
            return path
    raise SampleError("Chrome or Chromium is needed to render samples")


def screenshot(chrome: str, page: pathlib.Path, output: pathlib.Path, profile: pathlib.Path) -> None:
    raw = page.with_suffix(".png")
    width, height = WINDOW_SIZE
    subprocess.run(
        [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={profile}",
         f"--window-size={width},{height}", f"--force-device-scale-factor={SCALE}", f"--screenshot={raw}", page.as_uri()],
        check=True, capture_output=True,
    )
    image = Image.open(raw).convert("RGB")
    left, top, right, bottom = ImageChops.difference(image, Image.new("RGB", image.size, "white")).getbbox()
    margin = MARGIN_PX * SCALE
    image = image.crop((max(left - margin, 0), max(top - margin, 0), right + margin, bottom + margin))
    image.save(output, quality=QUALITY)


def render_samples(deck: pathlib.Path, output_dir: pathlib.Path) -> None:
    chrome = find_chrome()
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as workspace:
        root = pathlib.Path(workspace)
        package = Package(deck, root)
        for sample in SAMPLES:
            try:
                note_type, fields = package.note(sample.note_type, sample.note)
            except SampleError as error:
                logger.warning("Skipping %s: %s", sample.file, error)
                continue
            front, back = note_type.templates[sample.card]
            page = root / f"{pathlib.Path(sample.file).stem}.html"
            page.write_text(sample_page(render_template(front, fields), render_template(back, fields), note_type.css))
            screenshot(chrome, page, output_dir / sample.file, root / "profile")
            logger.info("Rendered %s", output_dir / sample.file)
