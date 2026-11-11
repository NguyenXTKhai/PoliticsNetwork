"""Crawl diplomatic news from The Guardian Open Platform API (English, full text).

Usage:
    python -m src.crawl.guardian --from 2024-01-01
    python -m src.crawl.guardian --from 2023-01-01 --to 2023-12-31

Free developer key: 60 calls/minute, 500 calls/day, 200 articles per call.
Output: data/raw/guardian.jsonl
"""
from __future__ import annotations

import argparse
import os
from datetime import date

from tqdm import tqdm

from .common import JsonlStore, clean_text, get

API = "https://content.guardianapis.com/search"
QUERY = (
    '"president" OR "prime minister" OR "foreign minister" OR "secretary of state" OR '
    'summit OR talks OR sanctions OR "military aid" OR agreement OR treaty OR expelled OR '
    'NATO OR "United Nations" OR ASEAN OR "European Union" OR G7 OR G20'
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="from_date", default="2024-01-01")
    ap.add_argument("--to", dest="to_date", default=date.today().isoformat())
    ap.add_argument("--section", default="world", help="Guardian section(s), e.g. 'world|us-news|politics'")
    ap.add_argument("--max-pages", type=int, default=450, help="stay under the 500 calls/day quota")
    args = ap.parse_args()

    key = os.getenv("GUARDIAN_API_KEY")
    if not key:
        raise SystemExit("GUARDIAN_API_KEY is missing in .env")

    store = JsonlStore("guardian")
    params = {
        "api-key": key, "q": QUERY, "section": args.section,
        "from-date": args.from_date, "to-date": args.to_date,
        "page-size": 200, "order-by": "oldest", "show-fields": "bodyText,trailText",
    }
    first = get(API, params={**params, "page": 1}, delay=1.1).json()["response"]
    pages = min(first["pages"], args.max_pages)
    print(f"{first['total']} matching articles, {first['pages']} pages (fetching {pages}); already stored: {len(store)}")

    new = 0
    for page in tqdm(range(1, pages + 1), desc="guardian pages"):
        resp = first if page == 1 else get(API, params={**params, "page": page}, delay=1.1).json()["response"]
        for it in resp["results"]:
            text = clean_text(it.get("fields", {}).get("bodyText", ""))
            if len(text) < 300 or it["webUrl"] in store.seen:
                continue
            store.add({
                "url": it["webUrl"],
                "title": it["webTitle"],
                "date": it["webPublicationDate"][:10],
                "source": "guardian",
                "lang": "en",
                "section": it.get("sectionName", ""),
                "description": clean_text(it.get("fields", {}).get("trailText", "")),
                "text": text,
            })
            new += 1
    print(f"Done: +{new} new, {len(store)} total -> {store.path}")


if __name__ == "__main__":
    main()
