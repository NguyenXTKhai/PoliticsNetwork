"""Shared helpers for crawlers: HTTP session, JSONL store, article extraction."""
from __future__ import annotations

import json
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable

import requests
import trafilatura
from dotenv import load_dotenv
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
from tqdm import tqdm

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "data" / "raw"
load_dotenv(ROOT / ".env")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) PoliticsNetwork/0.1 (+https://github.com/NguyenXTKhai/PoliticsNetwork)"

# Slug keywords (Vietnamese without diacritics) that signal diplomatic news.
DIPLO_SLUG = re.compile(
    r"tong-thong|thu-tuong|chu-tich|quoc-vuong|nha-vua|ngoai-truong|bo-truong|tong-bi-thu|"
    r"hoi-dam|dien-dam|tiep-|gap-|tham-|thuong-dinh|hoi-nghi|dien-dan|"
    r"ky-ket|thoa-thuan|hiep-dinh|hiep-uoc|doi-tac|hop-tac|nang-cap|"
    r"vien-tro|ho-tro|khoan-vay|trung-phat|cam-van|truc-xuat|dai-su|ngoai-giao|"
    r"len-an|chi-trich|ung-ho|phan-doi|canh-bao|de-doa|"
    r"nato|lhq|lien-hop-quoc|hdba|asean|-eu-|-eu$|^eu-|g7|g20|brics|apec|imf|who|wto|iaea|icc"
)

_local = threading.local()


def session() -> requests.Session:
    """One requests.Session per thread."""
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.headers.update({"User-Agent": UA, "Accept-Language": "vi,en;q=0.8"})
        _local.s = s
    return _local.s


class HttpError(Exception):
    pass


@retry(
    retry=retry_if_exception_type((requests.RequestException, HttpError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential(min=2, max=30),
    reraise=True,
)
def get(url: str, *, params: dict | None = None, delay: float = 1.0, allow_404: bool = False) -> requests.Response | None:
    time.sleep(delay)  # politeness: per-thread delay before every request
    r = session().get(url, params=params, timeout=30)
    if r.status_code in (404, 410) and allow_404:
        return None
    if r.status_code == 429 or r.status_code >= 500:
        raise HttpError(f"{r.status_code} {url}")
    r.raise_for_status()
    return r


def slug(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".html")


def is_diplomatic_slug(url: str) -> bool:
    return bool(DIPLO_SLUG.search(slug(url)))


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def meta(html: str, prop: str) -> str:
    m = re.search(rf'<meta[^>]+(?:property|name)=["\']{re.escape(prop)}["\'][^>]+content=["\']([^"\']*)', html)
    return m.group(1).strip() if m else ""


def extract_article(html: str, url: str) -> dict | None:
    """Main text + metadata with trafilatura; None if no usable text."""
    doc = trafilatura.bare_extraction(html, url=url, with_metadata=True, favor_precision=True,
                                      include_comments=False, include_tables=False)
    if doc is None:
        return None
    d = doc if isinstance(doc, dict) else doc.as_dict()
    text = clean_text(d.get("text") or "")
    if len(text) < 300:
        return None
    return {
        "title": clean_text(d.get("title") or meta(html, "og:title")),
        "date": meta(html, "article:published_time")[:10] or (d.get("date") or ""),
        "section": meta(html, "article:section"),
        "description": clean_text(d.get("description") or ""),
        "text": text,
    }


class JsonlStore:
    """Append-only JSONL file; remembers URLs already saved so crawls are resumable."""

    def __init__(self, name: str):
        RAW.mkdir(parents=True, exist_ok=True)
        self.path = RAW / f"{name}.jsonl"
        self.lock = threading.Lock()
        self.seen: set[str] = set()
        if self.path.exists():
            with self.path.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        self.seen.add(json.loads(line)["url"])
                    except (json.JSONDecodeError, KeyError):
                        pass

    def __len__(self) -> int:
        return len(self.seen)

    def add(self, rec: dict) -> None:
        with self.lock:
            if rec["url"] in self.seen:
                return
            self.seen.add(rec["url"])
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def fetch_many(urls: Iterable[str], store: JsonlStore, build: Callable[[str], dict | None],
               workers: int = 3, desc: str = "articles") -> int:
    """Fetch & store articles in parallel; returns number of new records."""
    todo = [u for u in dict.fromkeys(urls) if u not in store.seen]
    ok = 0
    with ThreadPoolExecutor(workers) as pool, tqdm(total=len(todo), desc=desc) as bar:
        futures = {pool.submit(build, u): u for u in todo}
        for fut in as_completed(futures):
            bar.update()
            try:
                rec = fut.result()
            except Exception as e:  # noqa: BLE001 - one bad article must not stop the crawl
                tqdm.write(f"  skip {futures[fut]}: {e}")
                continue
            if rec:
                store.add(rec)
                ok += 1
    return ok
