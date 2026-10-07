"""Minimal Wallhaven API v1 client.

Docs: https://wallhaven.cc/help/api
Rate limit: 45 requests/minute. NSFW results require a valid API key.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable, Iterable

import requests

BASE = "https://wallhaven.cc/api/v1"
USER_AGENT = "wallpick/0.1"

SORTINGS: list[tuple[str, str]] = [
    ("date_added", "Latest"),
    ("toplist", "Toplist"),
    ("views", "Views"),
    ("favorites", "Favorites"),
    ("random", "Random"),
    ("relevance", "Relevance"),
]
TOP_RANGES = ["1d", "3d", "1w", "1M", "3M", "6M", "1y"]


class WallhavenError(RuntimeError):
    """Any failure talking to Wallhaven, with a message fit for a toast."""


class RateLimiter:
    """Sliding window limiter. Kept under Wallhaven's 45/min ceiling."""

    def __init__(self, limit: int = 40, period: float = 60.0) -> None:
        self.limit = limit
        self.period = period
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._hits and now - self._hits[0] > self.period:
                    self._hits.popleft()
                if len(self._hits) < self.limit:
                    self._hits.append(now)
                    return
                wait = self.period - (now - self._hits[0]) + 0.05
            time.sleep(max(wait, 0.05))


def purity_bits(sketchy: bool, nsfw: bool) -> str:
    """SFW is always on; the other two are the toggles."""
    return "1{}{}".format(int(bool(sketchy)), int(bool(nsfw)))


def category_bits(general: bool, anime: bool, people: bool) -> str:
    bits = "{}{}{}".format(int(general), int(anime), int(people))
    return bits if bits != "000" else "111"


def extension_for(wallpaper: dict) -> str:
    file_type = (wallpaper.get("file_type") or "").lower()
    if "png" in file_type:
        return ".png"
    if "webp" in file_type:
        return ".webp"
    if "gif" in file_type:
        return ".gif"
    path = wallpaper.get("path") or ""
    suffix = Path(path).suffix
    return suffix if suffix else ".jpg"


class Wallhaven:
    def __init__(self, api_key: str = "") -> None:
        self.api_key = api_key or ""
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        self.limiter = RateLimiter()

    def set_api_key(self, key: str) -> None:
        self.api_key = key or ""

    @property
    def has_key(self) -> bool:
        return bool(self.api_key.strip())

    # --- low level --------------------------------------------------------

    def _get(self, path: str, params: dict | None = None) -> dict:
        headers = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key

        self.limiter.acquire()
        try:
            response = self.session.get(
                BASE + path, params=params or {}, headers=headers, timeout=20
            )
        except requests.RequestException as exc:
            raise WallhavenError(f"Network error: {exc}") from exc

        if response.status_code == 401:
            raise WallhavenError(
                "Wallhaven rejected the API key (401). Sketchy/NSFW need a valid key."
            )
        if response.status_code == 429:
            raise WallhavenError("Rate limited by Wallhaven (429). Give it a minute.")
        if response.status_code != 200:
            raise WallhavenError(f"Wallhaven returned HTTP {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise WallhavenError("Malformed response from Wallhaven") from exc
        if not isinstance(payload, dict):
            raise WallhavenError("Unexpected response shape from Wallhaven")
        return payload

    # --- endpoints --------------------------------------------------------

    def search(
        self,
        query: str = "",
        page: int = 1,
        categories: str = "111",
        purity: str = "100",
        sorting: str = "date_added",
        order: str = "desc",
        top_range: str = "1M",
        atleast: str = "",
        ratios: str = "",
        seed: str = "",
    ) -> tuple[list[dict], dict]:
        params: dict[str, str | int] = {
            "page": max(1, int(page)),
            "categories": categories,
            "purity": purity,
            "sorting": sorting,
            "order": order,
        }
        if query.strip():
            params["q"] = query.strip()
        if sorting == "toplist":
            params["topRange"] = top_range if top_range in TOP_RANGES else "1M"
        if sorting == "random" and seed:
            params["seed"] = seed
        if atleast.strip():
            params["atleast"] = atleast.strip()
        if ratios.strip():
            params["ratios"] = ratios.strip()

        payload = self._get("/search", params)
        data = payload.get("data") or []
        meta = payload.get("meta") or {}
        return [w for w in data if isinstance(w, dict)], meta

    def wallpaper(self, wallpaper_id: str) -> dict:
        return self._get(f"/w/{wallpaper_id}").get("data") or {}

    # --- media (static CDN, not part of the API rate limit) ---------------

    def fetch_bytes(self, url: str, timeout: int = 20) -> bytes:
        try:
            response = self.session.get(url, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise WallhavenError(f"Could not fetch image: {exc}") from exc
        return response.content

    def download(
        self,
        url: str,
        dest: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        """Stream a full-size image to `dest`, writing via a .part file."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        try:
            with self.session.get(url, stream=True, timeout=60) as response:
                response.raise_for_status()
                total = int(response.headers.get("content-length") or 0)
                done = 0
                with open(part, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=65536):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total)
            part.replace(dest)
        except requests.RequestException as exc:
            part.unlink(missing_ok=True)
            raise WallhavenError(f"Download failed: {exc}") from exc
        except OSError as exc:
            part.unlink(missing_ok=True)
            raise WallhavenError(f"Could not write file: {exc}") from exc
        return dest


def flatten_tags(wallpaper: dict) -> Iterable[str]:
    for tag in wallpaper.get("tags") or []:
        if isinstance(tag, dict) and tag.get("name"):
            yield str(tag["name"])
