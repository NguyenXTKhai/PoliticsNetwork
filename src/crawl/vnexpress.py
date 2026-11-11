"""Crawl VnExpress "Thế giới" (World) section via its date-range archive.

Usage:
    python -m src.crawl.vnexpress --since 2024-01 --workers 3
    python -m src.crawl.vnexpress --since 2026-01 --all       # no slug keyword filter

Output: data/raw/vnexpress.jsonl
"""
from __future__ import annotations

import argparse
import re
from datetime import datetime, timedelta, timezone

from tqdm import tqdm

from .common import JsonlStore, extract_article, fetch_many, get, is_diplomatic_slug

CATE_ID = 1001002  # Thế giới
ARCHIVE = "https://vnexpress.net/category/day/cateid/{cate}/fromdate/{f}/todate/{t}/allcate/0/page/{p}"
LINK = re.compile(r'https://vnexpress\.net/[a-z0-9-]+-\d{6,}\.html')
VN_TZ = timezone(timedelta(hours=7))


def windows(since: str, days: int):
    """Consecutive date windows. The archive only serves ~20 pages (~580 links) per query,
    so windows must be short enough (a week ~ 250-350 World articles)."""
    cur = datetime.strptime(since, "%Y-%m").replace(tzinfo=VN_TZ)
    now = datetime.now(VN_TZ)
    while cur <= now:
        nxt = cur + timedelta(days=days)
        yield cur, min(nxt - timedelta(seconds=1), now)
        cur = nxt


def list_month(start: datetime, end: datetime, max_pages: int) -> list[str]:
    urls: list[str] = []
    for p in range(1, max_pages + 1):
        html = get(ARCHIVE.format(cate=CATE_ID, f=int(start.timestamp()), t=int(end.timestamp()), p=p), delay=0.7).text
        found = [u for u in dict.fromkeys(LINK.findall(html)) if u not in urls]
        if not found:
            break
        urls += found
    return urls


def build(url: str) -> dict | None:
    r = get(url, delay=1.0, allow_404=True)
    if r is None:
        return None
    art = extract_article(r.text, url)
    if not art:
        return None
    return {"url": url, "source": "vnexpress", "lang": "vi", **art}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2024-01", help="YYYY-MM")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--days", type=int, default=7, help="archive window size in days")
    ap.add_argument("--max-pages", type=int, default=20, help="archive serves at most ~20 pages per window")
    ap.add_argument("--all", action="store_true", help="disable slug keyword filter")
    args = ap.parse_args()

    store = JsonlStore("vnexpress")
    print(f"already stored: {len(store)}")
    for start, end in windows(args.since, args.days):
        urls = list_month(start, end, args.max_pages)
        keep = urls if args.all else [u for u in urls if is_diplomatic_slug(u)]
        warn = "  (WARNING: page cap reached, lower --days)" if len(urls) >= 550 else ""
        tqdm.write(f"{start:%Y-%m-%d}: {len(urls)} links, {len(keep)} diplomatic{warn}")
        fetch_many(keep, store, build, workers=args.workers, desc=f"vnexpress {start:%Y-%m-%d}")
    print(f"Done: {len(store)} total -> {store.path}")


if __name__ == "__main__":
    main()
