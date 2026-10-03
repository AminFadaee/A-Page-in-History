import geopandas as gpd

from page_in_history.http import Http

COUNTRIES_URL = "https://naciscdn.org/naturalearth/50m/cultural/ne_50m_admin_0_countries.zip"
RIVERS_URL = "https://naciscdn.org/naturalearth/50m/physical/ne_50m_rivers_lake_centerlines.zip"
MAX_RIVER_RANK = 5


def countries(http: Http) -> gpd.GeoDataFrame:
    path = http.download(COUNTRIES_URL, "ne_50m_admin_0_countries.zip")
    frame = gpd.read_file(f"zip://{path}")
    frame["geometry"] = frame.geometry.buffer(0)
    return frame[["NAME_EN", "WIKIDATAID", "geometry"]].rename(columns={"NAME_EN": "name", "WIKIDATAID": "qid"})


def rivers(http: Http) -> gpd.GeoDataFrame:
    """Major rivers only: the Nile, Tigris, Euphrates, Indus, Oxus and the like."""
    path = http.download(RIVERS_URL, "ne_50m_rivers_lake_centerlines.zip")
    frame = gpd.read_file(f"zip://{path}")
    return frame[frame.scalerank <= MAX_RIVER_RANK][["name", "scalerank", "geometry"]]
