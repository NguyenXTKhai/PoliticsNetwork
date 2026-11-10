"""Step 4 - Relation extraction with an LLM through 9router (OpenAI-compatible API).

data/processed/sentences.jsonl -> data/processed/relations.jsonl

1. Select sentences: at most --per-pair sentences for each (entity pair, trigger type),
   preferring short sentences with few entities; rare relation types are always kept.
2. Send batches of --batch sentences. Each entity is given a local id (E1, E2, ...) so the
   model can only point to entities we linked - it cannot invent nodes.
3. Validate the JSON: known relation type, known entity ids, no self-loops,
   confidence >= --min-conf. Agreement / event names are kept as edge properties.
4. Append results; the run is resumable (already processed sids are skipped).

Usage:
    python src/extract_rel.py --limit 45            # quick test (3 batches)
    python src/extract_rel.py --workers 4           # full run
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue

from dotenv import load_dotenv
from openai import OpenAI
from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lexicon import RELATIONS  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROC = ROOT / "data" / "processed"
load_dotenv(ROOT / ".env")

RARE = {"EXPELLED_DIPLOMAT", "UPGRADED_PARTNERSHIP", "PHONE_CALL", "SANCTIONED"}
REDO_PER_ENTITY = 3  # max already-done sentences re-extracted per newly linked entity
RATE_WAITS = [0]  # number of free-tier rate-limit waits (shown in the progress bar)
CALL_LOG = Path(__file__).resolve().parents[1] / "data" / "processed" / "llm_calls.log"
_log_lock = threading.Lock()


def log_call(model: str, status: str, seconds: float, tokens: int = 0, note: str = "") -> None:
    """One TSV line per LLM call: time, model, status, latency, total tokens, note (used to study rate limits)."""
    with _log_lock, CALL_LOG.open("a", encoding="utf-8") as f:
        f.write("\t".join([time.strftime("%H:%M:%S"), model, status, f"{seconds:.1f}", str(tokens), note]) + "\n")

SYSTEM = """You are an expert annotator of diplomatic news (English and Vietnamese).
For each sentence, extract diplomatic ACTIONS between the listed entities only.

Relation types (source -> target):
- MET_WITH: source held talks / met / had a summit with target (in person or online).
- PHONE_CALL: source had a phone call with target.
- VISITED: source (person/country) made a visit to target (country/organization/person). Target = host.
- SIGNED_AGREEMENT: source signed an agreement, treaty, MoU, deal or joint statement with target.
- UPGRADED_PARTNERSHIP: source and target upgraded / established diplomatic relations or a strategic partnership.
- MILITARY_AID: source provided or pledged weapons / military assistance to target.
- ECONOMIC_AID: source provided or pledged economic, financial or humanitarian aid / loans to target.
- SANCTIONED: source imposed sanctions / embargo on target.
- EXPELLED_DIPLOMAT: source expelled diplomats of target, recalled its ambassador from target, or cut diplomatic ties with target.
- CONDEMNED: source condemned, criticised, accused or threatened target.
- SUPPORTED: source expressed support for, praised, welcomed or backed target.

Rules:
- Only relations EXPLICITLY stated in the sentence as having happened, happening, or officially agreed/pledged.
  Skip hypothetical, negated, rumoured, planned/future ("will meet", "is due to visit", "agreed to resume talks")
  or merely requested actions ("called for more aid").
- The TARGET must be the party that directly receives the action. Do NOT use an entity that is only the topic,
  location or victim of the context. Examples:
    "Russia supports proposals to end the war in Ukraine" -> NO relation (Ukraine is only the topic).
    "Russia launched missiles at Ukraine" -> NO relation (an attack is not aid, support or a sanction).
    "Iran's president Raisi said Iran supports the Palestinians" -> NO relation between Raisi and Iran.
    "Talks in Geneva between the US and Russia" -> US MET_WITH Russia (Switzerland is only the location).
- When unsure, output nothing: precision is more important than recall.
- Never relate an entity to itself, and never relate a person to his/her own country or organization
  (e.g. "US President Biden" -> no relation between Biden and United States).
- If a person acts on behalf of a country, use the PERSON as source/target when the person is listed.
- Use the entity ids given (E1, E2, ...). Do not create new entities.
- For SIGNED_AGREEMENT give "agreement" (short English name, e.g. "AUKUS", "Black Sea Grain Initiative") if named.
- For MET_WITH give "event" (short English name, e.g. "G20 Summit 2023") if a named summit/conference is mentioned.
- confidence: 0.0-1.0, how sure you are that the relation is explicitly stated.

Return ONLY JSON:
{"results": [{"id": <sentence number>, "relations": [{"source": "E1", "target": "E2", "relation": "MET_WITH",
  "confidence": 0.95, "agreement": null, "event": null}]}]}
Include every sentence id, with an empty list when there is no relation."""


# ---------------------------------------------------------------- selection
def select(rows: list[dict], per_pair: int, rare_cap: int) -> list[dict]:
    buckets = defaultdict(list)
    for i, r in enumerate(rows):
        qids = sorted({e["qid"] for e in r["entities"]})
        for a, b in itertools.combinations(qids, 2):
            for t in r["triggers"]:
                buckets[(a, b, t)].append(i)
    keep = set()
    for (a, b, t), idx in buckets.items():
        idx.sort(key=lambda i: (len(rows[i]["entities"]), len(rows[i]["sentence"])))
        keep.update(idx[:rare_cap] if t in RARE else idx[:per_pair])
    return [rows[i] for i in sorted(keep)]


# ---------------------------------------------------------------- LLM call
def entity_table(r: dict) -> tuple[list[str], dict[str, dict]]:
    local, lines, seen = {}, [], {}
    for e in r["entities"]:
        if e["qid"] in seen:
            continue
        lid = f"E{len(seen) + 1}"
        seen[e["qid"]] = lid
        local[lid] = e
        lines.append(f'{lid} = {e["name"]} ({e["type"]}; written as "{e["text"]}")')
    return lines, local


def build_prompt(batch: list[dict]) -> tuple[str, list[dict]]:
    parts, locals_ = [], []
    for n, r in enumerate(batch, 1):
        lines, local = entity_table(r)
        locals_.append(local)
        parts.append(f"[{n}] ({r['lang']}, {r['date']}) {r['sentence']}\n    " + "\n    ".join(lines))
    return "\n\n".join(parts), locals_


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise
        return json.loads(m.group())


# ---------------------------------------------------------------- post-validation rules
SYMMETRIC = {"MET_WITH", "PHONE_CALL", "SIGNED_AGREEMENT", "UPGRADED_PARTNERSHIP"}
# A relation is only accepted if the sentence contains a trigger word of a compatible type
# (the LLM sometimes reads "Russia attacked Ukraine with missiles" as MILITARY_AID).
COMPATIBLE = {
    "MET_WITH": {"MET_WITH", "VISITED", "PHONE_CALL"},
    "PHONE_CALL": {"PHONE_CALL", "MET_WITH"},
    "VISITED": {"VISITED", "MET_WITH"},
    "SIGNED_AGREEMENT": {"SIGNED_AGREEMENT", "UPGRADED_PARTNERSHIP"},
    "UPGRADED_PARTNERSHIP": {"UPGRADED_PARTNERSHIP", "SIGNED_AGREEMENT"},
    "MILITARY_AID": {"MILITARY_AID"},
    "ECONOMIC_AID": {"ECONOMIC_AID", "MILITARY_AID"},
    "SANCTIONED": {"SANCTIONED"},
    "EXPELLED_DIPLOMAT": {"EXPELLED_DIPLOMAT"},
    "CONDEMNED": {"CONDEMNED"},
    "SUPPORTED": {"SUPPORTED", "ECONOMIC_AID", "MILITARY_AID"},
}


def load_affiliations() -> set[tuple[str, str]]:
    """(person, country/org) pairs from Wikidata positions: a leader must not be related
    to the state or organization he/she represents ("Iran SUPPORTED Raisi")."""
    import csv
    with (ROOT / "data" / "entities" / "positions.csv").open(encoding="utf-8") as f:
        return {(r["person"], r["target"]) for r in csv.DictReader(f)}


class QuotaExhausted(RuntimeError):
    """The provider's free quota is used up for the day."""


class Extractor:
    """Runs the same prompt on 1..n models and keeps relations all of them agree on."""

    def __init__(self, models: list[str], min_conf: float):
        self.client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY") or "x",
                             timeout=240, max_retries=0)
        self.models, self.min_conf = models, min_conf
        self.affil = load_affiliations()

    def call(self, model: str, user: str) -> tuple[dict, str]:
        delay, errors = 5, 0
        while True:
            t0 = time.time()
            try:
                resp = self.client.chat.completions.create(
                    model=model, temperature=0,
                    messages=[{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                )
                log_call(model, "ok", time.time() - t0, resp.usage.total_tokens if resp.usage else 0)
                return parse_json(resp.choices[0].message.content or ""), resp.model
            except Exception as e:  # noqa: BLE001 - rate limits, timeouts, bad JSON -> retry
                msg = str(e)
                log_call(model, type(e).__name__, time.time() - t0, 0,
                         re.sub(r"\s+", " ", msg[msg.find("message"):][:120]) if "message" in msg else msg[:120])
                if any(s in msg for s in ("FreeUsageLimitError", "Free usage limit", "free-models-per-day")):
                    raise QuotaExhausted(model) from e  # daily quota, no reset time: retire this ensemble
                if "today's text quota" in msg:
                    # daily quota used up: provider allows 1 request / 3 min until the daily reset -> keep polling
                    RATE_WAITS[0] += 1
                    time.sleep(185)
                    continue
                m = re.search(r"reset after (?:(\d+)m\s*)?(\d+)s", msg)
                if "429" in msg and m:
                    # free-tier rate limit: wait exactly until the window resets (does not count as an error)
                    wait = int(m.group(1) or 0) * 60 + int(m.group(2)) + 3
                    RATE_WAITS[0] += 1
                    time.sleep(wait)
                    continue
                errors += 1
                if errors >= 6:
                    raise
                tqdm.write(f"  retry {errors} [{model}] after {type(e).__name__}: {msg[:100]}")
                time.sleep(delay)
                delay = min(delay * 2, 120)

    def parse(self, data: dict, batch: list[dict], locals_: list[dict]) -> list[dict[tuple, dict]]:
        """Per sentence: {(source, target, type): relation} after schema + rule validation.
        Rejected relations are returned under key ('REJECTED', reason, n)."""
        by_id = {}
        if isinstance(data, list):  # some answers are a bare list instead of {"results": [...]}
            data = {"results": data}
        for x in data.get("results", []):
            if not isinstance(x, dict):
                continue
            try:
                by_id[int(x.get("id", -1))] = x.get("relations") or []
            except (TypeError, ValueError):
                continue
        out = []
        for n, r in enumerate(batch, 1):
            loc, found = locals_[n - 1], {}
            for k, rel in enumerate(by_id.get(n, [])):
                s, t, typ = rel.get("source"), rel.get("target"), rel.get("relation")
                if typ not in RELATIONS or s not in loc or t not in loc or s == t:
                    continue
                es, et = loc[s], loc[t]
                if es["qid"] == et["qid"]:
                    continue
                try:
                    conf = float(rel.get("confidence") or 0)
                except (TypeError, ValueError):
                    conf = 0.0
                rec = {
                    "source": es["qid"], "source_name": es["name"], "source_type": es["type"],
                    "target": et["qid"], "target_name": et["name"], "target_type": et["type"],
                    "relation": typ, "confidence": round(conf, 2),
                    "agreement": rel.get("agreement") or None, "event": rel.get("event") or None,
                }
                reason = None
                if conf < self.min_conf:
                    reason = "low_confidence"
                elif (es["qid"], et["qid"]) in self.affil or (et["qid"], es["qid"]) in self.affil:
                    reason = "own_affiliation"
                elif not COMPATIBLE[typ] & set(r["triggers"]):
                    reason = "no_trigger"
                if reason:
                    found[("REJECTED", reason, k)] = rec
                    continue
                a, b = (es["qid"], et["qid"])
                if typ in SYMMETRIC and a > b:
                    a, b = b, a
                found[(a, b, typ)] = rec
            out.append(found)
        return out

    def run(self, batch: list[dict]) -> list[dict]:
        # cascade: a relation must be extracted by every model, so the other models only need to verify the
        # sentences where the first model proposed something (shorter prompts, far fewer tokens/requests)
        user, locals_ = build_prompt(batch)
        data0, model0 = self.call(self.models[0], user)
        parsed, served = [self.parse(data0, batch, locals_)], [model0]
        idx = [i for i, d in enumerate(parsed[0]) if any(k[0] != "REJECTED" for k in d)]
        others = self.models[1:]
        if others and idx:
            sub = [batch[i] for i in idx]
            sub_user, sub_locals = build_prompt(sub)
            with ThreadPoolExecutor(len(others)) as pool:
                answers = list(pool.map(lambda m: self.call(m, sub_user), others))
            for data, model in answers:
                full: list[dict] = [{} for _ in batch]
                for j, found in enumerate(self.parse(data, sub, sub_locals)):
                    full[idx[j]] = found
                parsed.append(full)
                served.append(model)
        else:
            parsed += [[{} for _ in batch] for _ in others]  # nothing to verify
            served += others
        out = []
        for n, r in enumerate(batch):
            per_model = [p[n] for p in parsed]
            keys = [{k for k in d if k[0] != "REJECTED"} for d in per_model]
            agreed = set.intersection(*keys) if keys else set()
            rels = []
            for k in sorted(agreed):
                rec = dict(per_model[0][k])
                rec["confidence"] = round(sum(d[k]["confidence"] for d in per_model) / len(per_model), 2)
                for d in per_model[1:]:  # keep agreement/event names from any model
                    rec["agreement"] = rec["agreement"] or d[k]["agreement"]
                    rec["event"] = rec["event"] or d[k]["event"]
                rels.append(rec)
            rejected = []
            for i, d in enumerate(per_model):
                for k, rec in d.items():
                    if k[0] == "REJECTED":
                        rejected.append({**rec, "model": served[i], "reason": k[1]})
                    elif k not in agreed:
                        rejected.append({**rec, "model": served[i], "reason": "no_agreement"})
            out.append({"sid": r["sid"], "url": r["url"], "source": r["source"], "lang": r["lang"],
                        "date": r["date"], "sentence": r["sentence"], "triggers": r["triggers"],
                        "models": served, "relations": rels, "rejected": rejected})
        return out


def coverage_first(rows: list[dict], todo: list[dict], done: set[str], out_path: Path,
                   rng: random.Random) -> list[dict]:
    """Put sentences that mention entities without any extracted relation yet at the front.

    Round-robin over uncovered entities (1st sentence of every entity, then the 2nd, ...), drawing also on
    sentences the per-pair cap of select() left out, so new nodes enter the graph as early as possible."""
    covered = set()
    for line in out_path.open(encoding="utf-8"):
        for x in json.loads(line)["relations"]:
            covered.update((x["source"], x["target"]))
    # entities each already-processed sentence had when it was extracted (to spot newly linked entities
    # after the dictionary was extended); written by preprocess.py as sentences_prev_entities.json
    prev_path = PROC / "sentences_prev_entities.json"
    prev = json.loads(prev_path.read_text(encoding="utf-8")) if prev_path.exists() else {}
    per_entity, redo = defaultdict(list), defaultdict(list)
    for r in rows:
        ents = {e["qid"] for e in r["entities"]} - covered
        if r["sid"] in done:
            # re-extract a few done sentences in which a new, still uncovered entity is now linked
            for q in ents - set(prev.get(r["sid"], ents)):
                redo[q].append(r)
            continue
        for q in ents:
            per_entity[q].append(r)
    for q, lst in redo.items():
        rng.shuffle(lst)
        per_entity[q] = lst[:REDO_PER_ENTITY] + per_entity[q]
    for q, lst in per_entity.items():
        if q not in redo:
            rng.shuffle(lst)
    front, seen = [], set()
    for k in range(max((len(v) for v in per_entity.values()), default=0)):
        for q in per_entity:
            if k < len(per_entity[q]) and per_entity[q][k]["sid"] not in seen:
                seen.add(per_entity[q][k]["sid"])
                front.append(per_entity[q][k])
    rest = [r for r in todo if r["sid"] not in seen]
    n_redo = sum(r["sid"] in done for r in front)
    print(f"coverage-first: {len(per_entity)} uncovered entities, {len(front)} sentences moved to the front "
          f"({n_redo} already-done sentences re-extracted because of newly linked entities)")
    return front + rest


# ---------------------------------------------------------------- main
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-pair", type=int, default=2, help="max sentences per (entity pair, trigger type)")
    ap.add_argument("--rare-cap", type=int, default=5, help="same, for rare relation types")
    ap.add_argument("--batch", type=int, default=15)
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--min-conf", type=float, default=0.7)
    ap.add_argument("--limit", type=int, help="process at most N selected sentences (testing)")
    ap.add_argument("--models", nargs="+", default=["agnes/agnes-2.5-flash", "agnes/agnes-3.0-flash"],
                    help="ensemble: a relation is kept only if every model extracts it")
    ap.add_argument("--ensemble", action="append", metavar="M1+M2",
                    help="run several ensembles in parallel on one shared queue (each gets --workers threads), "
                         "e.g. --ensemble agnes/agnes-2.5-flash+agnes/agnes-3.0-flash "
                         "--ensemble oc/deepseek-v4.1-flash-free+oc/glm-5.3-flash-free")
    ap.add_argument("--out", default="relations.jsonl")
    ap.add_argument("--no-coverage-first", action="store_true",
                    help="do not move sentences with not-yet-covered entities to the front of the queue")
    args = ap.parse_args()

    rows = [json.loads(line) for line in (PROC / "sentences.jsonl").open(encoding="utf-8")]
    selected = select(rows, args.per_pair, args.rare_cap)
    out_path = PROC / args.out
    done = set()
    if out_path.exists():
        done = {json.loads(line)["sid"] for line in out_path.open(encoding="utf-8")}
    todo = [r for r in selected if r["sid"] not in done]
    # random (seeded) order so that a partial run is representative across sources, languages and years
    rng = random.Random(2026)
    rng.shuffle(todo)
    if not args.no_coverage_first and out_path.exists():
        todo = coverage_first(rows, todo, done, out_path, rng)
    if args.limit:
        todo = todo[: args.limit]
    ensembles = [e.split("+") for e in args.ensemble] if args.ensemble else [args.models]
    print(f"{len(rows)} candidate sentences -> {len(selected)} selected; {len(done)} done; {len(todo)} to process "
          f"({len(todo) // args.batch + 1} batches) with ensembles {ensembles}, {args.workers} workers each")

    queue: Queue = Queue()
    for i in range(0, len(todo), args.batch):
        queue.put(todo[i:i + args.batch])
    lock = threading.Lock()
    n_rel = [0]
    per_ens = Counter()

    def worker(ex: Extractor, name: str, f, bar) -> None:
        # each ensemble pulls from the shared queue, so a faster (or less rate-limited) ensemble takes more batches
        while True:
            try:
                batch = queue.get_nowait()
            except Empty:
                return
            try:
                res = ex.run(batch)
            except QuotaExhausted as e:
                queue.put(batch)  # hand the batch back to the other ensembles
                tqdm.write(f"  [{name}] quota exhausted ({e}); this ensemble stops")
                return
            except Exception as e:  # noqa: BLE001 - skip the batch, it will be retried on the next run
                tqdm.write(f"  batch failed [{name}]: {type(e).__name__}: {str(e)[:150]}")
                bar.update(len(batch))
                continue
            with lock:
                for r in res:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    n_rel[0] += len(r["relations"])
                f.flush()
                per_ens[name] += len(batch)
                bar.update(len(batch))
                bar.set_postfix(relations=n_rel[0], rate_waits=RATE_WAITS[0],
                                **{k.split("/")[-1][:12]: v for k, v in per_ens.items()})

    with out_path.open("a", encoding="utf-8") as f, tqdm(total=len(todo), desc="sentences") as bar:
        threads = []
        for models in ensembles:
            ex, name = Extractor(models, args.min_conf), models[0]
            threads += [threading.Thread(target=worker, args=(ex, name, f, bar), daemon=True)
                        for _ in range(args.workers)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    print(f"+{n_rel[0]} relations -> {out_path.relative_to(ROOT)}; sentences per ensemble: {dict(per_ens)}")


if __name__ == "__main__":
    main()
