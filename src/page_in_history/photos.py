import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import quote

import numpy as np
from PIL import Image

from page_in_history.http import Http
from page_in_history.sources.wikidata import Entity

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
COMMONS_FILE = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width={}"
PHOTO_ERA = 1860
PHOTOGRAPHY_INVENTED = 1839
PHOTOGRAPH = "Q125191"
MAX_CANDIDATES = 10
BATCH = 50
SCORE_WIDTH = 480
ALONE = 0.9
WITH_EVIDENCE = 0.7
PORTRAIT = 0.5
IMAGE_FILE = re.compile(r"\.(jpe?g|png|tiff?)$", re.IGNORECASE)
NOT_A_PORTRAIT = re.compile(r"\bcoins?\b|coinage|\bdinars?\b|\baure(?:us|i)\b|siliqua|calligraph", re.IGNORECASE)
PHOTOGRAPHIC = re.compile(r"photograph|daguerreotype|albumen|gelatin silver|cartes? de visite|cabinet card|ambrotype|"
                          r"tintype|collodion", re.IGNORECASE)
NOT_PHOTOGRAPHIC = re.compile(r"painting|drawing|lithograph|engraving|etching|woodcut|illustration|sculpture|statue|"
                              r"\bbusts?\b|caricature|miniature|fresco|mosaic|\bcoins?\b|\bstamps?\b|calligraph",
                              re.IGNORECASE)
MACHINE_DATE = re.compile(r"QS:P\d*,\+(\d{3,4})-")
HUMAN_YEAR = re.compile(r"(?<!\d)(\d{3,4})(?!\d)")


class PhotoMode(StrEnum):
    MODEL = "model"
    METADATA = "metadata"


@dataclass(frozen=True)
class CommonsFile:
    title: str
    page_id: int | None
    categories: list[str]
    captured: str
    credit: str
    usage: int

    @property
    def name(self) -> str:
        return self.title.removeprefix("File:")

    @property
    def portrait(self) -> bool:
        return not any(NOT_A_PORTRAIT.search(category) for category in self.categories)


@dataclass(frozen=True)
class Photo:
    file: str
    credit: str
    check: str


class PhotoClassifier:
    """A zero-shot image model (OpenAI's CLIP ViT-L/14, 8-bit, run with onnxruntime on the CPU) that scores how much a
    picture looks like a photograph of one person rather than a painting, drawing or print, a photograph of a statue
    or painting, a group, or a picture of something else such as a building or a document."""

    REPOSITORY = "https://huggingface.co/Xenova/clip-vit-large-patch14/resolve/main/{}"
    FILES = {"vision": "onnx/vision_model_quantized.onnx", "text": "onnx/text_model.onnx",
             "tokenizer": "tokenizer.json"}
    PHOTO_PROMPTS = ["a photograph of a person", "a black and white photograph of a person",
                     "an old sepia studio photograph of a person"]
    OTHER_PROMPTS = ["a painting of a person", "an oil painting portrait", "an engraving of a person",
                     "a lithograph illustration of a person", "a drawing of a person",
                     "a photograph of a statue of a person", "a photograph of a marble bust", "a coin with a portrait"]
    PORTRAIT_PROMPTS = ["a portrait of one person"]
    SUBJECT_PROMPTS = ["a group of several people", "a building", "a document or manuscript"]
    SIZE = 224
    MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
    STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
    CONTEXT = 77
    PAD = 49407

    def __init__(self, http: Http):
        try:
            import onnxruntime
            from tokenizers import Tokenizer
        except ImportError as error:
            raise SystemExit("The photo model needs the optional ml extra: run 'uv sync --extra ml', or collect with "
                             "'--photos metadata' to use Commons metadata only") from error
        self.http = http
        paths = {part: http.download(self.REPOSITORY.format(file), f"clip-l14-{file.replace('/', '-')}")
                 for part, file in self.FILES.items()}
        self.vision = onnxruntime.InferenceSession(str(paths["vision"]), providers=["CPUExecutionProvider"])
        text = onnxruntime.InferenceSession(str(paths["text"]), providers=["CPUExecutionProvider"])
        tokenizer = Tokenizer.from_file(str(paths["tokenizer"]))
        tokenizer.enable_padding(length=self.CONTEXT, pad_id=self.PAD)
        tokenizer.enable_truncation(self.CONTEXT)
        def encode(prompts: list[str]) -> np.ndarray:
            ids = np.array([encoding.ids for encoding in tokenizer.encode_batch(prompts)], dtype=np.int64)
            return normalised(output(text, text.run(None, {"input_ids": ids}), "text_embeds"))
        self.medium = encode(self.PHOTO_PROMPTS + self.OTHER_PROMPTS)
        self.subject = encode(self.PORTRAIT_PROMPTS + self.SUBJECT_PROMPTS)
        self.scores_path = http.cache_dir / "photo-scores.json"
        self.scores = json.loads(self.scores_path.read_text()) if self.scores_path.exists() else {}

    def score(self, file: str) -> tuple[float, float]:
        """How much the picture looks like a photograph rather than art, and like a portrait of one person rather than
        a group, a building, a document or an object: each the share of the matching descriptions, cached per file."""
        if file not in self.scores:
            path = self.http.download(COMMONS_FILE.format(quote(file), SCORE_WIDTH), f"commons-{digest(file)}-{SCORE_WIDTH}")
            pixels = self._pixels(Image.open(path).convert("RGB"))[None]
            image = normalised(output(self.vision, self.vision.run(None, {"pixel_values": pixels}), "image_embeds"))
            self.scores[file] = [share(image, self.medium, len(self.PHOTO_PROMPTS)),
                                 share(image, self.subject, len(self.PORTRAIT_PROMPTS))]
            self.scores_path.write_text(json.dumps(self.scores))
        photo, portrait = self.scores[file]
        return photo, portrait

    def _pixels(self, image: Image.Image) -> np.ndarray:
        scale = self.SIZE / min(image.size)
        image = image.resize((round(image.width * scale), round(image.height * scale)), Image.BICUBIC)
        left, top = (image.width - self.SIZE) // 2, (image.height - self.SIZE) // 2
        image = image.crop((left, top, left + self.SIZE, top + self.SIZE))
        array = (np.asarray(image, dtype=np.float32) / 255 - self.MEAN) / self.STD
        return array.transpose(2, 0, 1)


class PortraitFinder:
    """Pictures of a person from Wikimedia Commons: an illustration for the Who card and a photograph for the Photo
    card, which can be different files."""

    def __init__(self, http: Http, classifier: PhotoClassifier | None):
        self.http = http
        self.classifier = classifier

    def illustration(self, person: Entity) -> tuple[str, str, str]:
        """The person's main Wikidata image unless Commons files it under coins or calligraphy, since those images
        can show someone else; its file name, credit, and why it was left out when it was."""
        main = [claim["mainsnak"]["datavalue"]["value"] for claim in person.statements("P18")][:1]
        file = next(iter(self._files(["File:" + name for name in main]).values()), None)
        if file is None:
            return "", "", "no image"
        if not file.portrait:
            return "", "", "image is a coin or calligraphy"
        return file.name, file.credit, ""

    def photograph(self, person: Entity) -> tuple[Photo | None, str]:
        """A photograph of the person for the Photo card. Candidates are the person's Wikidata images and the files
        in their own Commons category, tried in order: Wikidata's first, then the files most used across Wikipedia's
        articles. A candidate is a photograph when the model is sure on its own (0.9), or fairly sure (0.7) and
        Commons agrees; without the model, a photographic Commons category must say so."""
        births, deaths = person.years("P569"), person.years("P570")
        if not deaths or deaths[0].value < PHOTO_ERA:
            return None, f"no death year in or after {PHOTO_ERA}"
        lifetime = (births[0].earliest if births else deaths[0].latest - 100, deaths[0].latest)
        main = {"File:" + claim["mainsnak"]["datavalue"]["value"].replace("_", " ") for claim in person.statements("P18")}
        checked = []
        for file in self._candidates(person)[:MAX_CANDIDATES]:
            strong = self._photographic_category(file, person, file.title in main)
            weak = self._supporting(file, lifetime)
            if self.classifier:
                score, portrait = self.classifier.score(file.name)
                evidence = strong or weak
                found = f"model {score:.2f}, portrait {portrait:.2f}" + (f", {evidence}" if evidence else "")
                if portrait >= PORTRAIT and (score >= ALONE or (score >= WITH_EVIDENCE and evidence)):
                    return Photo(file.name, file.credit, found), ""
                checked.append(f"{file.name}: {found}")
            elif strong:
                return Photo(file.name, file.credit, strong), ""
            else:
                checked.append(f"{file.name}: {weak or 'no photographic category'}")
        return None, "; ".join(checked) or "no candidate image"

    def _candidates(self, person: Entity) -> list[CommonsFile]:
        main = [claim["mainsnak"]["datavalue"]["value"] for claim in person.statements("P18", preferred_only=False)]
        category = [claim["mainsnak"]["datavalue"]["value"] for claim in person.statements("P373")][:1]
        own = self._category_files(category[0]) if category else []
        files = self._files(["File:" + name for name in main] + own)
        main_titles = {"File:" + name.replace("_", " ") for name in main}
        ranked = sorted(files.values(), key=lambda file: (file.title not in main_titles, -file.usage))
        return [file for file in ranked if file.portrait and IMAGE_FILE.search(file.title)]

    def _category_files(self, category: str) -> list[str]:
        params = {"action": "query", "list": "categorymembers", "cmtitle": f"Category:{category}", "cmtype": "file",
                  "cmlimit": "max", "format": "json", "formatversion": 2}
        members = self.http.json(COMMONS_API, params, namespace="commons").get("query", {}).get("categorymembers", [])
        return [member["title"] for member in members]

    def _files(self, titles: list[str]) -> dict[str, CommonsFile]:
        """Metadata, visible categories and the number of Wikipedia articles using each file."""
        found: dict[str, dict] = {}
        unique = list(dict.fromkeys(title.replace("_", " ") for title in titles))
        for start in range(0, len(unique), BATCH):
            params = {"action": "query", "titles": "|".join(unique[start:start + BATCH]),
                      "prop": "imageinfo|categories|globalusage", "iiprop": "extmetadata", "clshow": "!hidden",
                      "cllimit": "max", "gunamespace": 0, "gulimit": "max", "format": "json", "formatversion": 2}
            while True:
                data = self.http.json(COMMONS_API, params, namespace="commons")
                for page in data.get("query", {}).get("pages", []):
                    if "missing" in page:
                        continue
                    entry = found.setdefault(page["title"], {"page": page, "categories": [], "usage": 0})
                    entry["categories"] += [category["title"] for category in page.get("categories", [])]
                    entry["usage"] += len(page.get("globalusage", []))
                    if "imageinfo" in page:
                        entry["page"] = page
                if "continue" not in data:
                    break
                params = {**params, **data["continue"]}
        files = {}
        for title, entry in found.items():
            metadata = entry["page"].get("imageinfo", [{}])[0].get("extmetadata", {})
            artist = plain(metadata.get("Artist", {}).get("value", ""))
            licence = metadata.get("LicenseShortName", {}).get("value", "")
            files[title] = CommonsFile(title, entry["page"].get("pageid"), entry["categories"],
                                       plain(metadata.get("DateTimeOriginal", {}).get("value", "")),
                                       " · ".join(part for part in (artist, licence) if part), entry["usage"])
        return files

    @staticmethod
    def _photographic_category(file: CommonsFile, person: Entity, main: bool) -> str:
        """A Commons category that names a photographic process, when none names a painting, print or sculpture. For
        files other than the person's main Wikidata image it must also be about portraits or about the person, since
        their Commons category also holds photographs of their house, grave or writings."""
        if any(NOT_PHOTOGRAPHIC.search(category) for category in file.categories):
            return ""
        name = (person.label or "").casefold()
        for category in file.categories:
            if PHOTOGRAPHIC.search(category) and (main or "portrait" in category.casefold() or name in category.casefold()):
                return f"in {category}"
        return ""

    def _supporting(self, file: CommonsFile, lifetime: tuple[int, int]) -> str:
        """Weaker signs, which only back the model: Commons classifies the file as a photograph, which it also does
        for photographs of paintings, or it was captured after 1839 during the person's lifetime, which also holds for
        prints of the time."""
        if any(NOT_PHOTOGRAPHIC.search(category) for category in file.categories):
            return ""
        if self._classified(file):
            return "classified as a photograph on Commons"
        year = capture_year(file.captured)
        if year is not None and max(lifetime[0], PHOTOGRAPHY_INVENTED) <= year <= lifetime[1]:
            return f"captured {year}, during their lifetime"
        return ""

    def _classified(self, file: CommonsFile) -> bool:
        if not file.page_id:
            return False
        media = self.http.json(COMMONS_API, {"action": "wbgetentities", "ids": f"M{file.page_id}", "format": "json"},
                               namespace="commons")
        statements = media.get("entities", {}).get(f"M{file.page_id}", {}).get("statements") or {}
        kinds = [claim["mainsnak"].get("datavalue", {}).get("value", {}).get("id") for claim in statements.get("P31", [])]
        return PHOTOGRAPH in kinds


def share(image: np.ndarray, prompts: np.ndarray, first: int) -> float:
    """The softmax weight of the first descriptions among all of them, at CLIP's usual scale of 100."""
    logits = 100 * image @ prompts.T
    weights = np.exp(logits - logits.max())
    return float(weights[0, :first].sum() / weights.sum())


def output(session, outputs: list, name: str) -> np.ndarray:
    return outputs[[meta.name for meta in session.get_outputs()].index(name)]


def normalised(vectors: np.ndarray) -> np.ndarray:
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def digest(file: str) -> str:
    return hashlib.sha1(file.encode()).hexdigest()[:16]


def capture_year(captured: str) -> int | None:
    """The year from a Commons capture date: its machine-readable form ('QS:P571,+1863-11-08…') when present,
    otherwise the first three- or four-digit number ('8 November 1863', 'circa 1890', '1922年11月15日')."""
    match = MACHINE_DATE.search(captured) or HUMAN_YEAR.search(captured)
    return int(match.group(1)) if match else None


def plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", html)).strip()
