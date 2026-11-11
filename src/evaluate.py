"""Step 7 - Precision evaluation by manual annotation.

1) Sample relation instances (one sentence + one extracted triple), stratified by relation type:
       python src/evaluate.py sample --n 300
   -> reports/eval_sample.csv  (columns label_a / label_b are left empty)
   Both team members independently fill label_a / label_b with 1 (correct) or 0 (wrong).
   A triple is correct when the sentence explicitly states that relation, with the right
   direction, between exactly these two entities, and both entities are linked correctly.

   KB-enriched nodes (persons added from Wikidata because they are mentioned in the news) are checked
   separately - is the mention in the sentence really this person, and is the edge right?
       python src/evaluate.py sample-kb --n 60   -> reports/eval_kb_sample.csv

2) Score:
       python src/evaluate.py score [--file reports/eval_kb_sample.csv]
   -> precision overall and per type (Wilson 95% CI), Cohen's kappa between annotators,
      reports/eval_result.json
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path

from build_graph import QC_REQUIRE, current_links

ROOT = Path(__file__).resolve().parents[1]
ENT, PROC, REP = ROOT / "data" / "entities", ROOT / "data" / "processed", ROOT / "reports"
SAMPLE = REP / "eval_sample.csv"
KB_SAMPLE = REP / "eval_kb_sample.csv"
STRATA = REP / "eval_strata.json"
GRAPH = ROOT / "data" / "graph"
FIELDS = ["eid", "relation", "source", "target", "sentence", "lang", "date", "url", "models", "confidence",
          "label_a", "label_b", "note"]


def names() -> dict[str, str]:
    out = {}
    for f in ("persons.csv", "countries.csv", "organizations.csv"):
        with (ENT / f).open(encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                out[r["qid"]] = r["name"]
    return out


def sample(n: int, seed: int) -> None:
    nm = names()
    by_type = defaultdict(list)
    latest = {}  # keep the latest record of re-extracted sentences
    for line in (PROC / "relations.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        latest[r["sid"]] = r
    links = current_links()
    for r in latest.values():
        for i, x in enumerate(r["relations"]):
            cue = QC_REQUIRE.get(x["relation"])
            if x["confidence"] < 0.7 or (cue and not cue.search(r["sentence"])) \
                    or not {x["source"], x["target"]} <= links.get(r["sid"], set()):
                continue  # same filters as build_graph: only relations that are in the graph
            by_type[x["relation"]].append((r, x, i))
    rng = random.Random(seed)
    total = sum(len(v) for v in by_type.values())
    # proportional allocation with a floor of 10 per type (or all if fewer)
    alloc = {t: min(len(v), max(10, round(n * len(v) / total))) for t, v in by_type.items()}
    rows = []
    for t, items in sorted(by_type.items()):
        for r, x, i in rng.sample(items, alloc[t]):
            rows.append({"eid": f"{r['sid']}#{i}", "relation": t,
                         "source": f"{nm.get(x['source'], x['source'])} ({x['source']})",
                         "target": f"{nm.get(x['target'], x['target'])} ({x['target']})",
                         "sentence": r["sentence"], "lang": r["lang"], "date": r["date"], "url": r["url"],
                         "models": "+".join(r.get("models", [])), "confidence": x["confidence"],
                         "label_a": "", "label_b": "", "note": ""})
    rng.shuffle(rows)
    REP.mkdir(exist_ok=True)
    with SAMPLE.open("w", encoding="utf-8-sig", newline="") as f:  # BOM so Excel shows Vietnamese correctly
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    # population size per stratum, for the stratified (population-weighted) precision estimate
    STRATA.write_text(json.dumps({t: len(v) for t, v in sorted(by_type.items())}, indent=1), encoding="utf-8")
    print(f"{len(rows)} instances from {total} -> {SAMPLE.relative_to(ROOT)}")
    print({t: alloc[t] for t in sorted(alloc)})


def sample_kb(n: int, seed: int) -> None:
    nm = names()
    kb = [x for x in json.loads((GRAPH / "nodes.json").read_text(encoding="utf-8")) if x.get("origin") == "kb"]
    rng = random.Random(seed)
    pick = {x["id"] for x in rng.sample(kb, min(n, len(kb)))}
    edges = {}
    for e in json.loads((GRAPH / "edges.json").read_text(encoding="utf-8")):
        if e["source"] in pick:
            edges.setdefault(e["source"], e)
    sents = {}
    for line in (PROC / "sentences.jsonl").open(encoding="utf-8"):
        r = json.loads(line)
        for x in r.get("entities") or []:
            if x["qid"] in pick and x.get("via") == "alias":
                sents.setdefault(x["qid"], []).append((r, x["text"]))
    rows = []
    for q in sorted(pick):
        r, text = rng.choice(sents[q])
        e = edges[q]
        rows.append({"eid": f"kb:{q}", "relation": f"{e['type']} ({e.get('title', '')})",
                     "source": f"{nm.get(q, q)} ({q}) <- mention '{text}'",
                     "target": f"{nm.get(e['target'], e['target'])} ({e['target']})",
                     "sentence": r["sentence"], "lang": r["lang"], "date": r["date"], "url": r["url"],
                     "models": "wikidata", "confidence": 1.0, "label_a": "", "label_b": "", "note": ""})
    with KB_SAMPLE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} KB-enriched nodes from {len(kb)} -> {KB_SAMPLE.relative_to(ROOT)}")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return round(c - h, 4), round(c + h, 4)


def kappa(a: list[int], b: list[int]) -> float:
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0


def score(path: Path, labels: Path | None = None) -> None:
    with path.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if labels:  # score a separate reference labelling (e.g. LLM-assisted check) as a single annotator
        with labels.open(encoding="utf-8-sig") as f:
            ref = {r["eid"]: r["label_claude"] for r in csv.DictReader(f)}
        rows = [{**r, "label_a": ref.get(r["eid"], ""), "label_b": "", "note": ""} for r in rows]
    rows = [r for r in rows if r["label_a"].strip() or r["label_b"].strip()]
    both = [r for r in rows if r["label_a"].strip() and r["label_b"].strip()]
    final, gold_of = [], {}  # gold label: annotator A where B is missing; disagreements count as wrong unless resolved in note
    for r in rows:
        la, lb = r["label_a"].strip(), r["label_b"].strip()
        if la and lb and la != lb:
            gold = 1 if r["note"].strip().lower().startswith("ok") else 0
        else:
            gold = int(la or lb)
        ens = "+".join(sorted(m.split("/")[-1] for m in r["models"].split("+") if m))
        final.append((r["relation"], r["lang"], gold, ens))
        gold_of[r["eid"]] = gold
    res = {"annotated": len(final), "double_annotated": len(both)}
    k = sum(row[2] for row in final)
    res["precision"] = round(k / len(final), 4)
    res["precision_ci95"] = wilson(k, len(final))
    # stratified estimate: per-type precision weighted by the type's share of all relations in the graph
    # (the sample over-represents rare types because of the floor of 10 per type)
    if STRATA.exists() and path == SAMPLE:
        pop = json.loads(STRATA.read_text(encoding="utf-8"))
        per = defaultdict(list)
        for row in final:
            per[row[0]].append(row[2])
        N = sum(pop[t] for t in per)
        p_w = sum(pop[t] / N * sum(v) / len(v) for t, v in per.items())
        var = sum((pop[t] / N) ** 2 * (sum(v) / len(v)) * (1 - sum(v) / len(v)) / len(v) for t, v in per.items())
        res["precision_weighted"] = round(p_w, 4)
        res["precision_weighted_ci95"] = [round(p_w - 1.96 * var ** 0.5, 4), round(min(1.0, p_w + 1.96 * var ** 0.5), 4)]
    # kappa / agreement must use the INDEPENDENT labels: label_b in eval_sample.csv may have been revised during
    # adjudication, so the original blind labels are read from the subset file when it exists
    blind_file = REP / "eval_subset_B.csv"
    if both and path == SAMPLE and blind_file.exists():
        with blind_file.open(encoding="utf-8-sig") as f:
            blind = {r["eid"]: r["label_b"].strip() for r in csv.DictReader(f) if r["label_b"].strip()}
        both = [{**r, "label_b": blind.get(r["eid"], r["label_b"])} for r in both]
        res["revised_b_labels"] = sum(1 for r in rows if r["eid"] in blind and r["label_b"].strip() != blind[r["eid"]])
    if both:
        la, lb = [int(r["label_a"]) for r in both], [int(r["label_b"]) for r in both]
        res["cohen_kappa"] = kappa(la, lb)
        res["agreement"] = round(sum(x == y for x, y in zip(la, lb)) / len(both), 4)
        dis = [r for r in both if r["label_a"].strip() != r["label_b"].strip()]
        res["disagreements"] = len(dis)
        res["disagreements_resolved_correct"] = sum(gold_of[r["eid"]] for r in dis)  # final (adjudicated) label
        res["subset_precision_a"] = round(sum(la) / len(la), 4)
        res["subset_precision_b"] = round(sum(lb) / len(lb), 4)
        res["subset_precision_gold"] = round(sum(gold_of[r["eid"]] for r in both) / len(both), 4)
    for key, idx in (("per_relation", 0), ("per_lang", 1), ("per_ensemble", 3)):
        groups = defaultdict(list)
        for row in final:
            groups[row[idx]].append(row[2])
        res[key] = {g: {"n": len(v), "precision": round(sum(v) / len(v), 4), "ci95": wilson(sum(v), len(v))}
                    for g, v in sorted(groups.items())}
    out = path.name.replace("sample.csv", "result.json")
    if labels:
        out = out.replace(".json", "_" + labels.stem.split("_")[-1] + ".json")
    (REP / out).write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=2))


def merge_b(subset: Path) -> None:
    """Copy label_b / note_b from the blind subset file (annotator B) into eval_sample.csv by eid."""
    with subset.open(encoding="utf-8-sig") as f:
        b = {r["eid"]: r for r in csv.DictReader(f) if r["label_b"].strip()}
    with SAMPLE.open(encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        if r["eid"] in b:
            r["label_b"] = b[r["eid"]]["label_b"].strip()
            if b[r["eid"]]["note_b"].strip():
                r["note"] = (r["note"] + " | B: " + b[r["eid"]]["note_b"].strip()).strip(" |")
    with SAMPLE.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"merged {len(b)} labels from {subset.name}; disagreements:",
          sum(1 for r in rows if r["label_b"] and r["label_a"] and r["label_a"] != r["label_b"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--n", type=int, default=300)
    s.add_argument("--seed", type=int, default=2026)
    k = sub.add_parser("sample-kb")
    k.add_argument("--n", type=int, default=60)
    k.add_argument("--seed", type=int, default=2026)
    c = sub.add_parser("score")
    c.add_argument("--file", type=Path, default=SAMPLE)
    c.add_argument("--labels", type=Path, help="score a separate label file (columns eid, label_claude)")
    m = sub.add_parser("merge-b")
    m.add_argument("--file", type=Path, default=REP / "eval_subset_B.csv")
    args = ap.parse_args()
    if args.cmd == "merge-b":
        merge_b(args.file)
    elif args.cmd == "sample":
        sample(args.n, args.seed)
    elif args.cmd == "sample-kb":
        sample_kb(args.n, args.seed)
    else:
        score(args.file, args.labels)


if __name__ == "__main__":
    main()
