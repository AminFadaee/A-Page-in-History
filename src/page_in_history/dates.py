import re
from dataclasses import dataclass

from page_in_history.naming import year_label

CENTURY = re.compile(r"(\d{1,2})(?:st|nd|rd|th)[\s-]+century\s*(BCE|BC)?", re.IGNORECASE)
ERA_FIRST = re.compile(r"\b(AD|CE)\s*(\d{1,4})\b")
ERA_LAST = re.compile(r"\b(\d{1,4})\s*(BCE|BC|CE|AD)\b")
BARE = re.compile(r"\b(\d{1,4})\b")
CIRCA = re.compile(r"\b(c\.|ca\.|circa)", re.IGNORECASE)
RANGE_SEPARATOR = re.compile(r"\s*[–—]\s*|\s+-\s+|\s+to\s+")


@dataclass(frozen=True)
class Span:
    """A year known to lie between earliest and latest, both inclusive; negative years are BC."""

    earliest: int
    latest: int
    circa: bool = False

    @property
    def precise(self) -> bool:
        return self.earliest == self.latest

    @property
    def width(self) -> int:
        return self.latest - self.earliest

    def label(self) -> str:
        if self.precise:
            return f"{'c. ' if self.circa else ''}{year_label(self.earliest)}"
        return century_label(self.earliest)

    def overlaps(self, other: "Span", tolerance: int) -> bool:
        return self.earliest - tolerance <= other.latest and other.earliest - tolerance <= self.latest


def century_label(year: int) -> str:
    """'3rd century', '6th century BC'."""
    century = (year - 1) // 100 + 1 if year > 0 else (-year + 99) // 100
    suffix = "th" if 10 <= century % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(century % 10, "th")
    return f"{century}{suffix} century{' BC' if year <= 0 else ''}"


def century(year: int) -> int:
    """Signed century: 3 for AD 201–300, -6 for 600–501 BC."""
    return (year - 1) // 100 + 1 if year > 0 else -((-year + 99) // 100)


def centuries(earliest: int, latest: int) -> list[int]:
    return [number for number in range(century(earliest), century(latest) + 1) if number != 0]


def century_span(century: int, before_christ: bool) -> Span:
    return Span(-century * 100, -century * 100 + 99) if before_christ else Span((century - 1) * 100 + 1, century * 100)


def parse(text: str, before_christ: bool = False) -> Span | None:
    """One date as written in an infobox: '1894 BC', 'c. 550 BC', '7th century BC', 'AD 395', '224'."""
    circa = bool(CIRCA.search(text))
    if match := CENTURY.search(text):
        return century_span(int(match.group(1)), bool(match.group(2)) or before_christ)
    if match := ERA_FIRST.search(text):
        year = int(match.group(2))
        return Span(year, year, circa)
    if match := ERA_LAST.search(text):
        year = int(match.group(1))
        year = -year if match.group(2).upper() in ("BC", "BCE") else year
        return Span(year, year, circa)
    if match := BARE.search(text):
        year = -int(match.group(1)) if before_christ else int(match.group(1))
        return Span(year, year, circa)
    return None


def parse_range(text: str) -> tuple[Span | None, Span | None]:
    """'27 BC – AD 395', '550–330 BC': a bare year takes the era written on the other side when that is BC."""
    parts = RANGE_SEPARATOR.split(text.strip(), maxsplit=1)
    if len(parts) != 2:
        return None, None
    start_text, end_text = parts
    end_before_christ = bool(re.search(r"\bBCE?\b", end_text))
    start_has_era = bool(re.search(r"\b(BCE?|AD|CE)\b", start_text))
    start = parse(start_text, before_christ=end_before_christ and not start_has_era)
    return start, parse(end_text)


def period_label(start: Span | None, end: Span | None) -> str:
    """'550–330 BC', '247 BC – AD 224', '224–651', '7th century BC – 168 BC'."""
    if start is None or end is None:
        return ""
    if start.precise and end.precise:
        circa = "c. " if start.circa or end.circa else ""
        if start.earliest < 0 and end.earliest < 0:
            return f"{circa}{-start.earliest}–{-end.earliest} BC"
        if start.earliest > 0:
            return f"{circa}{start.earliest}–{end.earliest}"
    return f"{start.label()} – {end.label()}"
