"""Step 5a - Build the graph (nodes.csv / edges.csv) from extracted relations.

data/processed/relations.jsonl + data/entities/*  ->  data/graph/{nodes,edges}.json + .csv

* Diplomatic edges (11 types) are aggregated per (source, target, type):
  weight = number of supporting sentences, first/last date, mean confidence,
  sources, languages and up to 3 evidence sentences with URLs.
  Symmetric types (MET_WITH, PHONE_CALL, SIGNED_AGREEMENT, UPGRADED_PARTNERSHIP) are stored once
  with the endpoints in canonical order.
* Agreement / Event nodes come from the "agreement" / "event" names the LLM gave:
  (Entity)-[:PARTY_TO]->(Agreement), (Entity)-[:ATTENDED]->(Event).
* Structural edges from Wikidata, only between nodes already in the graph:
  (Person)-[:LEADER_OF {role, title, start, end}]->(Country|Organization),
  (Country)-[:MEMBER_OF]->(Organization).
* KB enrichment (--kb-min-mentions N, 0 = off): a person who is linked by full name in >= N news
  sentences and holds a Wikidata position at a node already in the graph is added with that
  structural edge (node origin = "kb"; all other nodes have origin = "news").

Usage:
    python src/build_graph.py --min-weight 1
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENT, PROC, OUT = ROOT / "data" / "entities", ROOT / "data" / "processed", ROOT / "data" / "graph"
SYMMETRIC = {"MET_WITH", "PHONE_CALL", "SIGNED_AGREEMENT", "UPGRADED_PARTNERSHIP"}
LEADER_ROLES = {"head_of_state", "head_of_government", "foreign_minister", "defence_minister",
                "vice_president", "org_head", "minister", "parliament_speaker"}
STRUCT_TYPE = {r: "LEADER_OF" for r in LEADER_ROLES} | {"ambassador": "AMBASSADOR_OF"}
# post-hoc quality rules: a relation of these types is dropped unless its sentence has one of the cue words
# (found by manual error analysis, e.g. "summoned the ambassador" is not an expulsion)
QC_REQUIRE = {
    "EXPELLED_DIPLOMAT": re.compile(
        r"expel|expuls|recall|withdr[ae]w|cut .*ties|sever|break .*relations|broke off|persona non grata|"
        r"trục xuất|triệu hồi|rút đại sứ|cắt đứt|cắt quan hệ|đoạn tuyệt|đọan tuyệt|rời khỏi|bị buộc", re.I),
}

# Agreement / Event names made only of these words are generic ("joint statement", "Summit", "hội đàm"):
# they would merge unrelated events into one node, so they are not turned into nodes.
GENERIC = set("""
a an and the of on in for with to at between new first second third historic official state bilateral multilateral
joint statement statements declaration communique agreement agreements accord accords deal deals pact treaty treaties
memorandum memoranda understanding mou mous moc fta ftas cooperation cooperative comprehensive strategic partnership
partnerships framework protocol document documents instrument instruments plan roadmap initiative upgrade upgraded
free trade economic security defence defense military peace ceasefire nuclear investment visa
summit summits meeting meetings talks visit visits ceremony conference conferences session forum dialogue call phone
leaders leader ministers minister ministerial foreign level high working
hoi dam nghi thuong dinh chuyen tham hiep dinh thoa thuan tuyen bo chung ban ghi nho hop tac doi tac chien luoc toan dien
van kien ky ket le cap cao chinh thuc nha nuoc gap go dien
""".split())


GENERIC |= set("""
action support financial loan loans guarantee guarantees news press important quan trong virtual international
farewell monday tuesday wednesday thursday friday saturday sunday today yesterday talk
""".split())

# Event / Agreement names found in the manual check of the graph that are not events or agreements
# (places, policies, generic phrases, LLM output "null")
NOT_EVENT = {"white-house", "white-house-host", "shanghai", "null", "oda", "trade-bans", "arms-embargo",
             "diplomatic-relations", "normalization-of-relations", "upgraded-diplomatic-missions-to-embassy-level",
             "next-generation-mobility", "presidential-transition", "wto-accession", "icj-ruling",
             "friday-s-resolution", "rafale-fighter-jets-and-combat-helicopters", "water-is-life", "nexus",
             "financial-aid-package"}
# surface variants of the same recurring event -> one canonical name
EVENT_ALIAS = {"davos": "World Economic Forum", "wef": "World Economic Forum", "wef-davos": "World Economic Forum",
               "world-economic-forum-in-davos": "World Economic Forum", "world-economic-forum-davos": "World Economic Forum",
               "unga": "UN General Assembly", "united-nations-general-assembly": "UN General Assembly",
               "un-general-assembly": "UN General Assembly", "g20": "G20 Summit", "g7": "G7 Summit",
               "apec": "APEC Summit", "european-council": "European Council summit", "nafta": "NAFTA"}
# "37 agreements", "các văn kiện hợp tác": a count of documents, not one agreement
AGGREGATE = re.compile(r"^(?:\d+|two|three|four|five|six|seven|eight|nine|ten|several|multiple|various|many|dozens|"
                       r"các|nhiều)\s+(?!-)\D*(?:agreements|accords|deals|documents|instruments|mous|văn kiện)\b", re.I)


def is_generic(name: str) -> bool:
    return not (set(slug(name.replace("đ", "d").replace("Đ", "D")).split("-")) - GENERIC - {""})


def canonical_named(name: str, org_slugs: set[str]) -> str | None:
    """Canonical display name of an Agreement / Event mention, or None if it should not become a node."""
    if len(name) < 3 or is_generic(name) or AGGREGATE.search(name):
        return None
    key = slug(name)
    if key in NOT_EVENT:
        return None
    name = EVENT_ALIAS.get(key, name)
    if slug(name) in org_slugs:  # e.g. "North American Free Trade Agreement" is already an Organization node
        return None
    return name


def read_csv(name: str) -> list[dict]:
    with (ENT / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def slug(text: str) -> str:
    t = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", t).strip("-")


def tokens(text: str) -> set[str]:
    return {w for w in slug(text).split("-") if len(w) >= 3}


LINKS = PROC / "entity_links.jsonl.gz"


def iter_links():
    """Entity links per sentence: from sentences.jsonl when present (git-ignored, ~200 MB), otherwise from the
    committed compact copy entity_links.jsonl.gz (sid + entities only), so the graph can be rebuilt from a clone."""
    full = PROC / "sentences.jsonl"
    if full.exists():
        with full.open(encoding="utf-8") as f:
            yield from map(json.loads, f)
    else:
        with gzip.open(LINKS, "rt", encoding="utf-8") as f:
            yield from map(json.loads, f)


def write_links() -> None:
    """sentences.jsonl -> entity_links.jsonl.gz (called at the end of preprocess.py)."""
    with (PROC / "sentences.jsonl").open(encoding="utf-8") as f, gzip.open(LINKS, "wt", encoding="utf-8") as g:
        for line in f:
            r = json.loads(line)
            ents = [{k: x.get(k) for k in ("qid", "type", "via", "text")} for x in r.get("entities") or []]
            g.write(json.dumps({"sid": r["sid"], "entities": ents}, ensure_ascii=False) + "\n")


def current_links() -> dict[str, set[str]]:
    """sid -> entities linked in the current sentences.jsonl. A relation whose endpoint is no longer linked
    (alias table fixed after the sentence was extracted) is dropped."""
    return {r["sid"]: {x["qid"] for x in r.get("entities") or []} for r in iter_links()}


def kb_mentions() -> Counter:
    """Sentences per person linked by a full-name alias whose text shares a word with the canonical
    name (drops role-phrase aliases such as 'Minister of Foreign Affairs' and surname-only links)."""
    names = {r["qid"]: tokens(r["name"]) | tokens(r.get("name_vi", "")) for r in read_csv("persons.csv")}
    out = Counter()
    for r in iter_links():
        seen = {x["qid"] for x in r.get("entities") or []
                if x["type"] == "Person" and x.get("via") == "alias" and tokens(x["text"]) & names.get(x["qid"], set())}
        out.update(seen)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-weight", type=int, default=1, help="drop diplomatic edges with fewer sentences")
    ap.add_argument("--min-conf", type=float, default=0.7)
    ap.add_argument("--kb-min-mentions", type=int, default=3,
                    help="add news-mentioned persons with a Wikidata position at a graph node (0 = off)")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    info = {}
    for fname, typ in (("persons.csv", "Person"), ("countries.csv", "Country"), ("organizations.csv", "Organization")):
        for r in read_csv(fname):
            info[r["qid"]] = {"id": r["qid"], "label": typ, "name": r["name"], "name_vi": r.get("name_vi", ""),
                              "description": r.get("description", ""), "iso3": r.get("iso3", "")}

    org_slugs = {slug(v["name"]) for v in info.values() if v["label"] == "Organization"}

    # ---------------- aggregate diplomatic edges
    agg: dict[tuple, dict] = {}
    named = defaultdict(lambda: {"members": Counter(), "dates": [], "kind": ""})  # agreement/event nodes
    n_sent = n_qc = 0
    by_ensemble = Counter()
    latest = {}  # a sentence re-extracted after the dictionary was extended: keep its latest record
    for line in (PROC / "relations.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        latest[r["sid"]] = r
    links = current_links()
    for r in latest.values():
        n_sent += 1
        by_ensemble["+".join(sorted(m.split("/")[-1] for m in r.get("models", [])))] += 1
        for x in r["relations"]:
            if x["confidence"] < args.min_conf:
                continue
            cue = QC_REQUIRE.get(x["relation"])
            if (cue and not cue.search(r["sentence"])) or not {x["source"], x["target"]} <= links.get(r["sid"], set()):
                n_qc += 1
                continue
            s, t, typ = x["source"], x["target"], x["relation"]
            if typ in SYMMETRIC and s > t:
                s, t = t, s
            e = agg.setdefault((s, t, typ), {"source": s, "target": t, "type": typ, "weight": 0, "conf": [],
                                             "dates": [], "sources": Counter(), "langs": Counter(), "evidence": []})
            e["weight"] += 1
            e["conf"].append(x["confidence"])
            if r["date"]:
                e["dates"].append(r["date"])
            e["sources"][r["source"]] += 1
            e["langs"][r["lang"]] += 1
            if len(e["evidence"]) < 3:
                e["evidence"].append({"sentence": r["sentence"], "url": r["url"], "date": r["date"]})
            for kind, key in (("Agreement", "agreement"), ("Event", "event")):
                name = canonical_named((x.get(key) or "").strip(), org_slugs)
                if name:
                    nid = f"{'agr' if kind == 'Agreement' else 'evt'}:{slug(name)}"
                    node = named[nid]
                    node.update(kind=kind)
                    node.setdefault("names", Counter())[name] += 1
                    node["members"].update([x["source"], x["target"]])
                    if r["date"]:
                        node["dates"].append(r["date"])

    edges = []
    for e in agg.values():
        if e["weight"] < args.min_weight:
            continue
        d = sorted(e["dates"])
        edges.append({
            "source": e["source"], "target": e["target"], "type": e["type"], "weight": e["weight"],
            "confidence": round(sum(e["conf"]) / len(e["conf"]), 3), "directed": e["type"] not in SYMMETRIC,
            "first_date": d[0] if d else "", "last_date": d[-1] if d else "",
            "sources": dict(e["sources"]), "langs": dict(e["langs"]), "evidence": e["evidence"],
        })
    in_graph = {e["source"] for e in edges} | {e["target"] for e in edges}

    # ---------------- agreement / event nodes
    extra_nodes = []
    for nid, n in named.items():
        members = [q for q in n["members"] if q in in_graph]
        if not members:
            continue
        d = sorted(n["dates"])
        extra_nodes.append({"id": nid, "label": n["kind"], "name": n["names"].most_common(1)[0][0],
                            "name_vi": "", "description": "", "iso3": "", "first_date": d[0] if d else ""})
        rel = "PARTY_TO" if n["kind"] == "Agreement" else "ATTENDED"
        for q in members:
            edges.append({"source": q, "target": nid, "type": rel, "weight": n["members"][q],
                          "confidence": 1.0, "directed": True, "first_date": d[0] if d else "",
                          "last_date": d[-1] if d else "", "sources": {}, "langs": {}, "evidence": []})

    # ---------------- KB enrichment: news-mentioned persons with a position at a graph node
    positions = read_csv("positions.csv")
    kb_nodes = set()
    if args.kb_min_mentions > 0:
        mentions = kb_mentions()
        kb_nodes = {p["person"] for p in positions
                    if p["person"] not in in_graph and p["target"] in in_graph and p["role"] in STRUCT_TYPE
                    and mentions[p["person"]] >= args.kb_min_mentions}
        for q in kb_nodes:
            info[q]["mentions"] = mentions[q]
    news_nodes = set(in_graph)
    in_graph |= kb_nodes

    # ---------------- structural edges (only between nodes already in the graph)
    # one edge per (person, target, type, position title); several terms and the double role of e.g. the US
    # president (head of state AND head of government) are merged into that edge (terms, roles, weight = #terms)
    n_struct = Counter()
    struct: dict[tuple, dict] = {}
    for p in positions:
        if p["person"] in in_graph and p["target"] in in_graph and p["role"] in STRUCT_TYPE:
            typ = STRUCT_TYPE[p["role"]]
            g = struct.setdefault((p["person"], p["target"], typ, p.get("title", "")), {"roles": set(), "terms": set()})
            g["roles"].add(p["role"])
            g["terms"].add((p["start"], p["end"]))
    for (person, target, typ, title), g in struct.items():
        terms = sorted(g["terms"])
        starts = [s for s, _ in terms if s]
        open_term = any(not e for _, e in terms)
        edges.append({"source": person, "target": target, "type": typ, "weight": len(terms),
                      "confidence": 1.0, "directed": True, "role": "|".join(sorted(g["roles"])), "title": title,
                      "terms": [f"{s}/{e}" for s, e in terms],
                      "first_date": min(starts) if starts else "",
                      "last_date": "" if open_term else max(e for _, e in terms),
                      "sources": {"wikidata": 1}, "langs": {}, "evidence": []})
        n_struct[typ] += 1
    for m in read_csv("memberships.csv"):
        if m["country"] in in_graph and m["org"] in in_graph:
            edges.append({"source": m["country"], "target": m["org"], "type": "MEMBER_OF", "weight": 1,
                          "confidence": 1.0, "directed": True, "first_date": "", "last_date": "",
                          "sources": {"wikidata": 1}, "langs": {}, "evidence": []})
            n_struct["MEMBER_OF"] += 1

    nodes = [info[q] for q in sorted(in_graph) if q in info] + extra_nodes
    degree = Counter()
    for e in edges:
        degree[e["source"]] += 1
        degree[e["target"]] += 1
    for n in nodes:
        n["degree"] = degree[n["id"]]
        n["origin"] = "kb" if n["id"] in kb_nodes else "news"

    (OUT / "nodes.json").write_text(json.dumps(nodes, ensure_ascii=False), encoding="utf-8")
    (OUT / "edges.json").write_text(json.dumps(edges, ensure_ascii=False), encoding="utf-8")
    with (OUT / "nodes.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["id", "label", "name", "name_vi", "description", "iso3", "degree", "origin"], extrasaction="ignore")
        w.writeheader()
        w.writerows(nodes)
    with (OUT / "edges.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, ["source", "target", "type", "weight", "confidence", "directed", "first_date", "last_date"],
                           extrasaction="ignore")
        w.writeheader()
        w.writerows(edges)

    print(f"{n_qc} relations dropped by QC rules (cue words, endpoints no longer linked)")
    print(f"{n_sent} processed sentences; by ensemble: {dict(by_ensemble.most_common())}")
    print(f"nodes: {len(nodes)}  {dict(Counter(n['label'] for n in nodes))}  "
          f"(news-extracted {len(news_nodes) + len(extra_nodes)}, kb-enriched {len(kb_nodes)})")
    print(f"edges: {len(edges)}  {dict(Counter(e['type'] for e in edges).most_common())}")
    print(f"-> {OUT.relative_to(ROOT)}/nodes.(json|csv), edges.(json|csv)")


if __name__ == "__main__":
    main()
