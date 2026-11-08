"""Crawl Báo Thế giới & Việt Nam (baoquocte.vn) via its monthly sitemaps.

Category listing pages are loaded through /ajax/ (disallowed by robots.txt), so we
use the public monthly sitemaps, pre-filter URLs by diplomatic slug keywords, and
keep the article section for later filtering.
Note: the site currently publishes no monthly sitemaps for 2025.

Usage:
    python -m src.crawl.baoquocte --since 2024-01 --workers 3
Output: data/raw/baoquocte.jsonl
"""
from __future__ import annotations

import argparse
import re

from tqdm import tqdm

from .common import JsonlStore, extract_article, fetch_many, get, is_diplomatic_slug

INDEXES = [
    "https://baoquocte.vn/sitemap-by-year-site-1.xml",
    "https://baoquocte.vn/sitemap-current-year-site-1.xml",
]
MONTH = re.compile(r"<loc>(https://baoquocte\.vn/stores/lookssitemaps/site_1/sitemap-month-(\d{4})-(\d{1,2})\.xml)</loc>")
LOC = re.compile(r"<loc>(https://baoquocte\.vn/[^<]+-\d{5,}\.html)</loc>")

# Sections that are clearly off-topic for a diplomacy network.
SKIP_SECTIONS = {"Halal", "Văn hóa", "Du lịch", "Thể thao", "Sức khỏe", "Bất động sản",
                 "Tài chính - Chứng khoán", "Thương hiệu - Sản phẩm", "Giải trí", "Ẩm thực"}


def monthly_sitemaps(since: str) -> list[tuple[str, str]]:
    y0, m0 = map(int, since.split("-"))
    out = {}
    for idx in INDEXES:
        for url, y, m in MONTH.findall(get(idx, delay=0.5).text):
            if (int(y), int(m)) >= (y0, m0):
                out[f"{y}-{int(m):02d}"] = url
    return sorted(out.items())


def build(url: str) -> dict | None:
    r = get(url, delay=1.0, allow_404=True)
    if r is None:
        return None
    art = extract_article(r.text, url)
    if not art or art["section"] in SKIP_SECTIONS:
        return None
    return {"url": url, "source": "baoquocte", "lang": "vi", **art}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-01", help="YYYY-MM")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--all", action="store_true", help="disable slug keyword filter")
    args = ap.parse_args()

    store = JsonlStore("baoquocte")
    sitemaps = monthly_sitemaps(args.since)
    print(f"{len(sitemaps)} monthly sitemaps: {[m for m, _ in sitemaps]}; already stored: {len(store)}")
    for month, sm in sitemaps:
        r = get(sm, delay=1.0, allow_404=True)
        urls = LOC.findall(r.text) if r is not None else []
        keep = urls if args.all else [u for u in urls if is_diplomatic_slug(u)]
        tqdm.write(f"{month}: {len(urls)} links, {len(keep)} diplomatic")
        fetch_many(keep, store, build, workers=args.workers, desc=f"baoquocte {month}")
    print(f"Done: {len(store)} total -> {store.path}")


if __name__ == "__main__":
    main()
