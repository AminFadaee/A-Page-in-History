import logging

import pandas as pd

from page_in_history.http import Http

URL = "https://data.sciencespo.fr/api/access/datafile/4432"
FILE = "bhht-cross-verified-database.csv.gz"
COLUMNS = ["wikidata_code", "birth", "un_subregion", "level1_main_occ", "level2_main_occ", "sum_visib_ln_5criteria"]
PERIODS = [(500, "before 500"), (1500, "500–1500"), (1750, "1500–1750"), (1900, "1750–1900"), (10_000, "after 1900")]
EXCLUDED_DOMAINS = {"Sports/Games", "Missing", "Other"}
EXCLUDED_FIELDS = {"Sports/Games", "Corporate/Executive/Business (large)", "Worker/Business (small)", "Family"}
UNKNOWN_REGION = "Unknown"

logger = logging.getLogger(__name__)


def period(birth: float) -> str:
    return next(name for end, name in PERIODS if birth <= end)


def load(http: Http) -> pd.DataFrame:
    """The cross-verified database of notable people (Laouenan et al., Scientific Data 2022): 2.29 million people
    with a Wikidata id, cross-checked across seven Wikipedia editions and Wikidata, each with a visibility score that
    combines language editions, page length, page views, external links and biographical completeness. Indexed by
    Wikidata id, with each person's region and period."""
    frame = pd.read_csv(http.download(URL, FILE), usecols=COLUMNS, encoding="utf-8", encoding_errors="replace",
                        low_memory=False)
    frame["birth"] = pd.to_numeric(frame.birth, errors="coerce")
    frame = frame.dropna(subset=["birth", "sum_visib_ln_5criteria"]).drop_duplicates("wikidata_code")
    frame["period"] = frame.birth.map(period)
    frame["region"] = frame.un_subregion.fillna(UNKNOWN_REGION)
    frame = frame.rename(columns={"sum_visib_ln_5criteria": "score"})
    logger.info("Read %d notable people", len(frame))
    return frame.set_index("wikidata_code")


def pool(frame: pd.DataFrame, adult_before: int, adult_age: int, per_cell: int) -> pd.DataFrame:
    """The candidates: people who were adults before the year, outside sport, business and family roles, ranked
    within their region and period, and among the first of their cell. Every part of the world and of history brings
    the same number of people, so the database's lean towards Europe and North America does not carry into the deck,
    and comparing people only with their own region and period keeps the many recent figures from crowding out the
    few ancient ones. Each keeps its rank in the cell."""
    eligible = frame[(frame.birth + adult_age < adult_before) & ~frame.level1_main_occ.isin(EXCLUDED_DOMAINS)
                     & ~frame.level2_main_occ.isin(EXCLUDED_FIELDS)].copy()
    eligible["cell_rank"] = eligible.groupby(["region", "period"]).score.rank(ascending=False, method="first")
    return eligible[eligible.cell_rank <= per_cell]
