import bz2
import csv
from dataclasses import dataclass

from page_in_history.http import Http

URL = "https://storage.googleapis.com/pantheon-public-data/person_2025_update.csv.bz2"


@dataclass(frozen=True)
class Person:
    occupation: str
    popularity: float | None
    birth_year: int | None
    birthplace: tuple[float, float] | None
    birth_country: str


def number(value: str) -> float | None:
    return float(value) if value else None


def load(http: Http) -> dict[str, Person]:
    path = http.download(URL, "pantheon_2025.csv.bz2")
    people = {}
    with bz2.open(path, "rt", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            longitude, latitude = number(row["bplace_lon"]), number(row["bplace_lat"])
            birth_year = number(row["birthyear"])
            people[row["wd_id"]] = Person(
                row["occupation"],
                number(row["hpi"]),
                int(birth_year) if birth_year is not None else None,
                (longitude, latitude) if longitude is not None and latitude is not None else None,
                row["bplace_country"],
            )
    return people
