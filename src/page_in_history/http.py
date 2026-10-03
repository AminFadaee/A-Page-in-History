import hashlib
import json
import logging
import pathlib
import time

import requests

USER_AGENT = "page-in-history/0.1 (https://github.com/AminFadaee; a.fadaee.94@gmail.com)"
RETRIES = 4
BACKOFF_SECONDS = 5
TIMEOUT_SECONDS = 180

logger = logging.getLogger(__name__)


class Http:
    """A requests session that caches JSON responses and downloads on disk, so reruns stay offline."""

    def __init__(self, cache_dir: pathlib.Path):
        self.cache_dir = cache_dir
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def json(self, url: str, params: dict | None = None, namespace: str = "http") -> dict:
        key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()
        path = self.cache_dir / namespace / key[:2] / f"{key}.json"
        if path.exists():
            return json.loads(path.read_text())
        data = self._get(url, params, accept="application/json").json()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def download(self, url: str, name: str) -> pathlib.Path:
        path = self.cache_dir / "downloads" / name
        if path.exists():
            return path
        path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("Downloading %s", url)
        partial = path.with_suffix(path.suffix + ".part")
        with self._get(url, None, stream=True) as response, partial.open("wb") as file:
            for chunk in response.iter_content(1 << 20):
                file.write(chunk)
        partial.rename(path)
        return path

    def _get(self, url: str, params: dict | None, accept: str | None = None, stream: bool = False) -> requests.Response:
        headers = {"Accept": accept} if accept else {}
        for attempt in range(RETRIES):
            try:
                response = self.session.get(url, params=params, headers=headers, timeout=TIMEOUT_SECONDS, stream=stream)
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(f"{response.status_code} from {url}", response=response)
                response.raise_for_status()
                return response
            except (requests.ConnectionError, requests.Timeout, requests.HTTPError) as error:
                if attempt == RETRIES - 1 or (error.response is not None and 400 <= error.response.status_code < 429):
                    raise
                logger.warning("Retrying %s after %s", url, error)
                time.sleep(BACKOFF_SECONDS * (attempt + 1))
        raise AssertionError("unreachable")
