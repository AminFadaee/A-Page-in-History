import pathlib
import zipfile

from page_in_history.http import Http

INTER_URL = "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"
INTER_MEMBER = "extras/ttf/Inter-SemiBold.ttf"


def inter(http: Http) -> pathlib.Path:
    """Inter SemiBold, under the SIL Open Font License."""
    target = http.cache_dir / "fonts" / pathlib.Path(INTER_MEMBER).name
    if not target.exists():
        archive = http.download(INTER_URL, "inter-4.1.zip")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zipfile.ZipFile(archive).read(INTER_MEMBER))
    return target
