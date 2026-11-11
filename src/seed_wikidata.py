"""Seed the entity dictionary from Wikidata.

Builds the candidate node set (heads of state / government, foreign ministers,
heads of international organizations, sovereign countries, international
organizations) plus structural edges (LEADER_OF, MEMBER_OF) and a bilingual
(en / vi) alias dictionary used later for entity linking.

Usage:
    python src/seed_wikidata.py               # since 2000
    python src/seed_wikidata.py --since 2010

Outputs (data/entities/):
    countries.csv, organizations.csv, persons.csv,
    positions.csv   person -> country/org with role, start, end
    memberships.csv country -> organization
    aliases.json    normalized alias -> [qid, ...]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import time
import unicodedata
from collections import defaultdict
from pathlib import Path

import requests
from tenacity import retry, stop_after_attempt, wait_exponential

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "entities"
SPARQL = "https://query.wikidata.org/sparql"
API = "https://www.wikidata.org/w/api.php"
UA = "PoliticsNetwork/0.1 (https://github.com/NguyenXTKhai/PoliticsNetwork)"

# Key organizations that must be present even if Wikidata typing misses them.
# Given as English Wikipedia titles and resolved to QIDs at runtime
# (hard-coded QIDs are error-prone).
CORE_ORGS = [
    "United Nations", "United Nations Security Council", "United Nations General Assembly",
    "NATO", "European Union", "European Commission", "ASEAN",
    "International Monetary Fund", "World Bank", "World Trade Organization",
    "World Health Organization", "International Atomic Energy Agency",
    "International Criminal Court", "Arab League", "African Union", "G7", "G20",
    "OPEC", "Organization for Security and Co-operation in Europe", "Council of Europe",
    "Shanghai Cooperation Organisation", "BRICS", "Asia-Pacific Economic Cooperation",
    "Gulf Cooperation Council", "Organization of American States",
    "Quadrilateral Security Dialogue", "Commonwealth of Nations",
    "Collective Security Treaty Organization", "Mekong–Lancang Cooperation",
    # UN bodies / courts / agencies and non-state actors that are frequent diplomatic actors
    "International Court of Justice", "United Nations Human Rights Council", "UNHCR", "UNRWA", "UNICEF",
    "UNESCO", "World Food Programme", "Food and Agriculture Organization", "International Committee of the Red Cross",
    "Interpol", "Asian Development Bank", "Asian Infrastructure Investment Bank", "New Development Bank",
    "European Parliament", "European Council", "Eurasian Economic Union", "Mekong River Commission",
    "Organisation for the Prohibition of Chemical Weapons", "Organisation of Islamic Cooperation",
    "Hamas", "Hezbollah", "Houthis", "Taliban", "Wagner Group", "Palestine Liberation Organization",
    "Palestinian Authority", "Amnesty International", "Human Rights Watch", "Médecins Sans Frontières",
    "Comprehensive and Progressive Agreement for Trans-Pacific Partnership",
    "Regional Comprehensive Economic Partnership", "ASEAN Regional Forum", "East Asia Summit",
]
MIN_ALIAS_LEN = 2  # shorter aliases are dropped
SHORT_ALIAS = 4    # aliases up to this length go to the case-sensitive dictionary

session = requests.Session()
session.headers["User-Agent"] = UA


# ---------------------------------------------------------------- helpers
@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=30))
def sparql(query: str) -> list[dict]:
    r = session.post(SPARQL, data={"query": query, "format": "json"}, timeout=180)
    r.raise_for_status()
    rows = []
    for b in r.json()["results"]["bindings"]:
        rows.append({k: v["value"].rsplit("/", 1)[-1] if v["type"] == "uri" else v["value"] for k, v in b.items()})
    return rows


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=30))
def get_entities(qids: list[str]) -> dict:
    r = session.get(
        API,
        params={
            "action": "wbgetentities",
            "ids": "|".join(qids),
            "props": "labels|aliases|descriptions",
            # "mul" = language-independent default label (Wikidata, since 2024);
            # many names like "Donald Trump" exist only there.
            "languages": "en|vi|mul",
            "format": "json",
        },
        timeout=60,
    )
    r.raise_for_status()
    data = r.json()
    if "error" in data:  # e.g. maxlag / rate limit -> let tenacity retry
        raise RuntimeError(data["error"])
    return data["entities"]


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=30))
def resolve_enwiki(titles: list[str]) -> dict[str, str]:
    """English Wikipedia title -> QID (follows redirects and title normalization)."""
    out = {}
    for i in range(0, len(titles), 50):
        batch = titles[i : i + 50]
        r = session.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "titles": "|".join(batch), "redirects": 1,
                    "prop": "pageprops", "ppprop": "wikibase_item", "format": "json"},
            timeout=60,
        )
        r.raise_for_status()
        q = r.json()["query"]
        hop = {d["from"]: d["to"] for d in q.get("normalized", []) + q.get("redirects", [])}
        final = {p["title"]: p.get("pageprops", {}).get("wikibase_item") for p in q["pages"].values()}
        for t in batch:
            cur = t
            while cur in hop:
                cur = hop[cur]
            if final.get(cur):
                out[t] = final[cur]
    missing = set(titles) - set(out)
    if missing:
        print(f"  WARNING: enwiki titles not found / redirected: {sorted(missing)}")
    return out


def norm(text: str) -> str:
    """Normalize an alias for dictionary lookup (NFC, lowercase, single spaces)."""
    text = unicodedata.normalize("NFC", text).lower().strip()
    text = re.sub(r"[\"'’“”()]", "", text)
    return re.sub(r"\s+", " ", text)


def year(date: str | None) -> str:
    return date[:10] if date else ""


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  -> {path.relative_to(ROOT)} ({len(rows)} rows)")


# ---------------------------------------------------------------- queries
def q_countries() -> list[dict]:
    return sparql("""
    SELECT DISTINCT ?qid ?iso3 WHERE {
      ?qid wdt:P31 wd:Q3624078 .
      FILTER NOT EXISTS { ?qid wdt:P576 ?dissolved }
      OPTIONAL { ?qid wdt:P298 ?iso3 }
    }""")


def q_heads(since: int) -> list[dict]:
    rows = []
    # P1906 = office held by head of state, P1313 = office held by head of government
    for prop, role in (("P1906", "head_of_state"), ("P1313", "head_of_government")):
        res = sparql(f"""
        SELECT ?person ?target ?pos ?start ?end WHERE {{
          ?target wdt:P31 wd:Q3624078 ; wdt:{prop} ?pos .
          ?person p:P39 ?st . ?st ps:P39 ?pos . ?person wdt:P31 wd:Q5 .  # real humans only (Wikidata also has fictional office holders)
          OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }}
          FILTER(!BOUND(?end) || YEAR(?end) >= {since})
        }}""")
        rows += [dict(r, role=role) for r in res]
    return rows


def q_foreign_ministers(since: int) -> list[dict]:
    return sparql(f"""
    SELECT ?person ?target ?pos ?start ?end WHERE {{
      ?pos wdt:P279 wd:Q7330070 ; wdt:P1001 ?target .
      ?target wdt:P31 wd:Q3624078 .
      ?person p:P39 ?st . ?st ps:P39 ?pos . ?person wdt:P31 wd:Q5 .  # real humans only (Wikidata also has fictional office holders)
      OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }}
      FILTER(!BOUND(?end) || YEAR(?end) >= {since})
    }}""")


def q_class_holders(cls: str, since: int) -> list[dict]:
    """Holders of national offices that are a direct subclass of `cls`
    (Q83307 minister -> 'Minister of Defence of X', 'Minister of Finance of X', ...;
     Q42178 vice president)."""
    return sparql(f"""
    SELECT ?person ?target ?pos ?start ?end WHERE {{
      ?pos wdt:P279 wd:{cls} ; wdt:P1001 ?target .
      ?target wdt:P31 wd:Q3624078 .
      ?person p:P39 ?st . ?st ps:P39 ?pos . ?person wdt:P31 wd:Q5 .
      OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }}
      FILTER(!BOUND(?end) || YEAR(?end) >= {since})
    }}""")


def q_ambassadors(since: int) -> list[dict]:
    """Ambassadors: holders of positions that are (transitive) subclasses of ambassador (Q121998);
    target = the sending country (P1001 of the position)."""
    return sparql(f"""
    SELECT ?person ?target ?pos ?start ?end WHERE {{
      ?pos wdt:P279+ wd:Q121998 ; wdt:P1001 ?target . ?target wdt:P31 wd:Q3624078 .
      ?person p:P39 ?st . ?st ps:P39 ?pos . ?person wdt:P31 wd:Q5 .
      OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }}
      FILTER(!BOUND(?end) || YEAR(?end) >= {since})
    }}""")


def q_speakers(since: int) -> list[dict]:
    """Presiding officers of national legislatures (country P194 legislature -> P2388 office)."""
    return sparql(f"""
    SELECT ?person ?target ?pos ?start ?end WHERE {{
      ?target wdt:P31 wd:Q3624078 ; wdt:P194 ?leg . ?leg wdt:P2388 ?pos .
      ?person p:P39 ?st . ?st ps:P39 ?pos . ?person wdt:P31 wd:Q5 .
      OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }}
      FILTER(!BOUND(?end) || YEAR(?end) >= {since})
    }}""")


def q_orgs() -> list[dict]:
    """International organizations with >= 3 sovereign-state members."""
    return sparql("""
    SELECT ?qid (COUNT(DISTINCT ?m) AS ?members) WHERE {
      ?qid wdt:P31/wdt:P279* wd:Q484652 .
      ?m wdt:P463 ?qid ; wdt:P31 wd:Q3624078 .
    } GROUP BY ?qid HAVING(COUNT(DISTINCT ?m) >= 3)""")


def q_memberships(org_qids: list[str]) -> list[dict]:
    values = " ".join(f"wd:{q}" for q in org_qids)
    return sparql(f"""
    SELECT DISTINCT ?country ?org WHERE {{
      VALUES ?org {{ {values} }}
      ?country wdt:P31 wd:Q3624078 ; p:P463 ?st . ?st ps:P463 ?org .
      FILTER NOT EXISTS {{ ?st pq:P582 ?end }}
    }}""")


def q_org_heads(org_qids: list[str], since: int) -> list[dict]:
    values = " ".join(f"wd:{q}" for q in org_qids)
    return sparql(f"""
    SELECT ?person ?target ?pos ?start ?end WHERE {{
      VALUES ?target {{ {values} }}
      ?target wdt:P2388 ?pos .
      ?person p:P39 ?st . ?st ps:P39 ?pos . ?person wdt:P31 wd:Q5 .  # real humans only (Wikidata also has fictional office holders)
      OPTIONAL {{ ?st pq:P580 ?start }} OPTIONAL {{ ?st pq:P582 ?end }}
      FILTER(!BOUND(?end) || YEAR(?end) >= {since})
    }}""")


# ---------------------------------------------------------------- main
LABEL_FIX = {
    "Q318493": "Najib Mikati",          # label vandalised as "Mohamed Agha"
    "Q98053664": "Mark Phillips",       # Guyanese PM, label vandalised as "Hugo Chávez"
    "Q131248": "Lee Kuan Yew",
    "Q57308": "Abdelaziz Bouteflika",
}
BAD_ALIAS = {"Q98053664": ["Hugo Chávez", "Hugo Chavez", "Chávez"], "Q318493": ["Mohamed Agha"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", type=int, default=2000, help="keep office terms ending in/after this year")
    ap.add_argument("--minister-since", type=int, default=2008,
                    help="same, for other cabinet ministers and vice presidents (much larger set)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    print("[1/6] Countries")
    countries = {r["qid"]: r for r in q_countries()}
    print(f"  {len(countries)} sovereign states")

    print("[2/6] Heads of state / government")
    positions = q_heads(args.since)

    print("[3/6] Foreign ministers")
    positions += [dict(r, role="foreign_minister") for r in q_foreign_ministers(args.since)]
    fm_keys = {(p["person"], p["pos"]) for p in positions}
    ministers = [dict(r, role="minister") for r in q_class_holders("Q83307", args.minister_since)
                 if (r["person"], r["pos"]) not in fm_keys]
    vps = [dict(r, role="vice_president") for r in q_class_holders("Q42178", args.minister_since)]
    print(f"  + {len(ministers)} other minister terms, {len(vps)} vice-president terms (since {args.minister_since})")
    positions += ministers + vps
    ambassadors = [dict(r, role="ambassador") for r in q_ambassadors(args.minister_since)]
    speakers = [dict(r, role="parliament_speaker") for r in q_speakers(args.minister_since)]
    print(f"  + {len(ambassadors)} ambassador terms, {len(speakers)} legislature-speaker terms")
    positions += ambassadors + speakers

    print("[4/6] International organizations")
    orgs = {r["qid"]: {"qid": r["qid"], "members": r["members"]} for r in q_orgs()}
    for qid in resolve_enwiki(CORE_ORGS).values():
        orgs.setdefault(qid, {"qid": qid, "members": ""})
    org_ids = sorted(orgs)
    memberships = q_memberships(org_ids)
    positions += [dict(r, role="org_head") for r in q_org_heads(org_ids, args.since)]
    print(f"  {len(orgs)} organizations, {len(memberships)} memberships")

    # keep only positions whose target is a known node
    positions = [p for p in positions if p["target"] in countries or p["target"] in orgs]
    persons = {p["person"] for p in positions}
    print(f"  {len(persons)} distinct persons, {len(positions)} position statements")

    print("[5/6] Labels & aliases (en/vi)")
    pos_ids = {p["pos"] for p in positions}
    all_ids = sorted(set(countries) | set(orgs) | persons | pos_ids)
    meta: dict[str, dict] = {}
    for i in range(0, len(all_ids), 50):
        meta.update(get_entities(all_ids[i : i + 50]))
        if i // 50 % 20 == 0:
            print(f"  {min(i + 50, len(all_ids))}/{len(all_ids)}")
        time.sleep(0.2)

    # vandalised / unusual English labels found during evaluation (the alias table must not inherit them)
    for qid, name in LABEL_FIX.items():
        if qid in meta:
            meta[qid].setdefault("labels", {})["en"] = {"language": "en", "value": name}

    def label(qid: str, lang: str) -> str:
        labels = meta.get(qid, {}).get("labels", {})
        for lg in (lang, "mul", "en"):  # fall back to the default / English label
            if lg in labels:
                return labels[lg]["value"]
        return ""

    # aliases     : long names, matched case-insensitively ("tập cận bình")
    # aliases_cs  : short names (<= SHORT_ALIAS chars), matched case-sensitively, because
    #               many collide with Vietnamese words ("nam", "anh", "đức") -> only "Anh", "NAM"
    aliases: dict[str, set[str]] = defaultdict(set)
    aliases_cs: dict[str, set[str]] = defaultdict(set)
    for qid, ent in meta.items():
        if qid in pos_ids and qid not in persons:  # office items are not graph nodes
            continue
        names = [v["value"] for v in ent.get("labels", {}).values()]
        names += [a["value"] for lst in ent.get("aliases", {}).values() for a in lst]
        for n in names:
            key = norm(n)
            if len(key) > SHORT_ALIAS:
                aliases[key].add(qid)
            elif len(key) >= MIN_ALIAS_LEN and n.isupper():  # keep acronyms only: "NATO", "WHO"
                aliases_cs[unicodedata.normalize("NFC", n.strip())].add(qid)

    # Manual Vietnamese aliases: alias -> English Wikipedia title (resolved to QID)
    manual = ROOT / "data" / "entities" / "manual_aliases.csv"
    if manual.exists():
        with manual.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        title2qid = resolve_enwiki(sorted({r["enwiki"] for r in rows}))
        for r in rows:
            if r["enwiki"] not in title2qid:
                continue
            qid, key = title2qid[r["enwiki"]], norm(r["alias"])
            if len(key) > SHORT_ALIAS:
                aliases[key] = {qid}  # manual wins
            else:
                aliases_cs[unicodedata.normalize("NFC", r["alias"].strip())] = {qid}

    for qid, bad in BAD_ALIAS.items():
        for n in bad:
            aliases.get(norm(n), set()).discard(qid)

    print("[6/6] Writing files")
    write_csv(
        OUT / "countries.csv",
        [{"qid": q, "name": label(q, "en"), "name_vi": label(q, "vi"), "iso3": c.get("iso3", "")} for q, c in countries.items()],
        ["qid", "name", "name_vi", "iso3"],
    )
    write_csv(
        OUT / "organizations.csv",
        [{"qid": q, "name": label(q, "en"), "name_vi": label(q, "vi"), "members": o["members"]} for q, o in orgs.items()],
        ["qid", "name", "name_vi", "members"],
    )
    write_csv(
        OUT / "persons.csv",
        [
            {
                "qid": q,
                "name": label(q, "en"),
                "name_vi": label(q, "vi"),
                "description": meta.get(q, {}).get("descriptions", {}).get("en", {}).get("value", ""),
            }
            for q in sorted(persons)
        ],
        ["qid", "name", "name_vi", "description"],
    )
    seen, pos_rows = set(), []
    for p in positions:
        title = label(p["pos"], "en")
        role = p["role"]
        if role == "minister" and re.search(r"defen[cs]e|armed forces|military", title, re.I):
            role = "defence_minister"
        key = (p["person"], p["target"], role, year(p.get("start")))
        if key not in seen:
            seen.add(key)
            pos_rows.append({**p, "role": role, "title": title, "title_vi": label(p["pos"], "vi"),
                             "start": year(p.get("start")), "end": year(p.get("end"))})
    write_csv(OUT / "positions.csv", pos_rows, ["person", "target", "role", "title", "title_vi", "pos", "start", "end"])
    write_csv(OUT / "memberships.csv", memberships, ["country", "org"])
    for name, d in (("aliases.json", aliases), ("aliases_cs.json", aliases_cs)):
        with (OUT / name).open("w", encoding="utf-8") as f:
            json.dump({k: sorted(v) for k, v in sorted(d.items())}, f, ensure_ascii=False, indent=0)
        ambiguous = sum(len(v) > 1 for v in d.values())
        print(f"  -> data/entities/{name} ({len(d)} aliases, {ambiguous} ambiguous)")


if __name__ == "__main__":
    main()
