import re
import unicodedata


def slugify(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")


def year_label(year: int) -> str:
    return f"{-year} BC" if year < 0 else f"AD {year}" if year < 1000 else str(year)
