import json
import logging
import pathlib
import shutil
from dataclasses import asdict, dataclass
from urllib.parse import quote

from PIL import Image

from page_in_history.diagrams import SuccessionDiagram
from page_in_history.figures import Figure, FigureBuilder
from page_in_history.http import Http
from page_in_history.maps import MapRenderer
from page_in_history.polities import Polity, PolityBuilder, PolityCatalog
from page_in_history.sources import fonts, naturalearth, pantheon
from page_in_history.sources.cliopatria import Cliopatria
from page_in_history.sources.wikidata import Wikidata
from page_in_history.sources.wikipedia import Wikipedia

IMAGE_WIDTH = 480
IMAGE_QUALITY = 85
COMMONS_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width={}"

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Slice:
    region: str
    start: int
    end: int


@dataclass(frozen=True)
class DataPaths:
    root: pathlib.Path

    @property
    def civilizations(self) -> pathlib.Path:
        return self.root / "civilizations"

    @property
    def figures(self) -> pathlib.Path:
        return self.root / "figures"

    @property
    def maps(self) -> pathlib.Path:
        return self.root / "maps"

    @property
    def successions(self) -> pathlib.Path:
        return self.root / "successions"

    @property
    def images(self) -> pathlib.Path:
        return self.root / "images"

    @property
    def report(self) -> pathlib.Path:
        return self.root / "report.json"


class Collector:
    def __init__(self, paths: DataPaths, cache_dir: pathlib.Path):
        self.paths = paths
        self.cache_dir = cache_dir
        self.http = Http(cache_dir)

    def run(self, scope: Slice) -> None:
        cliopatria = Cliopatria.load(self.http)
        wikipedia, wikidata = Wikipedia(self.http), Wikidata(self.http)
        countries = naturalearth.countries(self.http)
        catalog = PolityCatalog(cliopatria, wikipedia, wikidata, scope.start, scope.end)
        builder = PolityBuilder(catalog, countries)
        polities = builder.build(builder.select(scope.region, scope.start, scope.end))
        logger.info("Selected %d civilizations: %s", len(polities), ", ".join(polity.name for polity in polities))
        for directory in (self.paths.civilizations, self.paths.figures, self.paths.maps, self.paths.successions,
                          self.paths.images):
            shutil.rmtree(directory, ignore_errors=True)
            directory.mkdir(parents=True)
        font = fonts.inter(self.http)
        renderer = MapRenderer(cliopatria, countries, naturalearth.rivers(self.http), font)
        diagram = SuccessionDiagram(font)
        for polity in polities:
            polity.map = f"maps/{polity.slug}.png"
            capitals = [capital["location"] for capital in polity.capitals if capital["location"]]
            polity.checks["labelled"] = renderer.render(polity.name, polity.map_year, capitals,
                                                        self.paths.root / polity.map)
            if polity.succession:
                for side, reveal in (("question", False), ("answer", True)):
                    polity.succession[side] = f"successions/{polity.slug}-{side}.png"
                    diagram.render(polity.succession, polity.id, reveal, self.paths.root / polity.succession[side])
        figure_builder = FigureBuilder(catalog, pantheon.load(self.http), self.http)
        figures = figure_builder.build({polity.name for polity in polities}, scope.start, scope.end)
        for figure in figures:
            figure.image = self._image(figure)
        self._write(polities, figures)
        self._report(scope, catalog, cliopatria, polities, figures)

    def _image(self, figure: Figure) -> str:
        if not figure.image:
            return ""
        url = COMMONS_FILE.format(quote(figure.image), IMAGE_WIDTH)
        source = self.http.download(url, f"commons-{figure.slug}-{IMAGE_WIDTH}")
        target = self.paths.images / f"{figure.slug}.jpg"
        Image.open(source).convert("RGB").save(target, quality=IMAGE_QUALITY)
        return f"images/{target.name}"

    def _write(self, polities: list[Polity], figures: list[Figure]) -> None:
        for polity in polities:
            write_json(self.paths.civilizations / f"{polity.slug}.json", polity.to_json())
        for figure in figures:
            write_json(self.paths.figures / f"{figure.slug}.json", figure.to_json())
        logger.info("Wrote %d civilizations and %d figures", len(polities), len(figures))

    def _report(self, scope: Slice, catalog: PolityCatalog, cliopatria: Cliopatria, polities, figures) -> None:
        write_json(self.paths.report, {
            "slice": asdict(scope),
            "civilizations": len(polities),
            "figures": len(figures),
            "unresolved_polities": dict(sorted(catalog.unresolved.items())),
            "ignored_cliopatria_steps": [asdict(spike) for spike in cliopatria.spikes],
        })


def write_json(path: pathlib.Path, document: dict) -> None:
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def load_documents(directory: pathlib.Path) -> list[dict]:
    return [json.loads(path.read_text()) for path in sorted(directory.glob("*.json"))]
