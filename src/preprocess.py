"""Step 3 - Preprocessing & entity linking.

data/raw/*.jsonl  ->  data/processed/sentences.jsonl (+ preprocess_stats.json)

Pipeline per article:
  1. de-duplicate articles (URL, normalized title) and sentences (exact text)
  2. normalize text (Unicode NFC, Vietnamese tone-mark placement "hoà" -> "hòa")
  3. sentence split (spaCy sentencizer for English, underthesea for Vietnamese)
  4. dictionary entity linking against the Wikidata alias dictionaries
       - longest match first, case-insensitive for long names, case-sensitive for short ones
       - article-level surname aliases ("Macron" after "Emmanuel Macron" appeared)
       - role mentions resolved by article date ("Tổng thống Mỹ", "Ukraine's foreign minister"
         -> whoever held that office on that date, from positions.csv)
  5. keep sentences with >= 2 distinct entities AND >= 1 diplomatic trigger word

Usage:
    python src/preprocess.py                    # all sources
    python src/preprocess.py --sources guardian --limit 500
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent))
from lexicon import find_triggers  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENT = ROOT / "data" / "entities"
RAW = ROOT / "data" / "raw"
OUT = ROOT / "data" / "processed"

MAX_ALIAS_TOKENS = 8
MIN_SENT_CHARS, MAX_SENT_CHARS = 30, 800

# Generic titles that appear as Wikidata aliases of some entity but are common nouns in news.
STOP_ALIASES = {
    "prime minister", "president", "the president", "king", "queen", "emperor", "chancellor",
    "foreign minister", "foreign secretary", "secretary of state", "secretary-general", "secretary general",
    "security council", "general assembly", "commission", "parliament", "government", "council",
    "the commission", "the council", "union", "the union", "republic", "kingdom", "federation",
    "tổng thống", "thủ tướng", "chủ tịch", "chủ tịch nước", "ngoại trưởng", "quốc vương", "nhà vua",
    "tổng thư ký", "chính phủ", "quốc hội", "hội đồng", "liên minh",
}
# Vietnamese: an article-local short name ("Chính", "Putin") must follow one of these,
# otherwise "Chính phủ" would be linked to Phạm Minh Chính.
VI_HONORIFIC = re.compile(
    r"(?:ông|bà|ngài|Tổng thống|Thủ tướng|Chủ tịch|Ngoại trưởng|Bộ trưởng|Quốc vương|Nhà vua|"
    r"Tổng thư ký|Tổng Bí thư|Phó Thủ tướng|Phó Tổng thống)\s+$"
)

# ---------------------------------------------------------------- normalization
_TONE_FIX = {  # new-style -> old-style tone placement, applied to both aliases and text
    "oà": "òa", "oá": "óa", "oả": "ỏa", "oã": "õa", "oạ": "ọa",
    "oè": "òe", "oé": "óe", "oẻ": "ỏe", "oẽ": "õe", "oẹ": "ọe",
    "uỳ": "ùy", "uý": "úy", "uỷ": "ủy", "uỹ": "ũy", "uỵ": "ụy",
}
_TONE_RX = re.compile("|".join(_TONE_FIX))
TOKEN = re.compile(r"\w+(?:[-'’.]\w+)*")


def vn_norm(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace("’", "'")
    return _TONE_RX.sub(lambda m: _TONE_FIX[m.group()], text)


def tokens(text: str) -> list[re.Match]:
    return list(TOKEN.finditer(text))


def key_of(text: str, lower: bool = True) -> str:
    t = vn_norm(text)
    return " ".join(m.group() for m in TOKEN.finditer(t.lower() if lower else t))


# ---------------------------------------------------------------- resources (per worker)
R: dict = {}


def load_resources() -> None:
    def read(name):
        with (ENT / name).open(encoding="utf-8") as f:
            return list(csv.DictReader(f))

    etype, ename = {}, {}
    for fname, typ in (("persons.csv", "Person"), ("countries.csv", "Country"), ("organizations.csv", "Organization")):
        for r in read(fname):
            etype[r["qid"]] = typ
            ename[r["qid"]] = r["name"] or r["name_vi"] or r["qid"]

    def ok(alias_key: str, qid: str) -> bool:
        if qid not in etype or alias_key.lower() in STOP_ALIASES:
            return False
        if etype[qid] == "Person" and len(alias_key.split()) == 1:
            # one-word person alias must be a non-first token of the name ("Putin", "Lula"),
            # never a given name ("David", "Boris")
            name_toks = key_of(ename[qid]).split()
            return alias_key.lower() in name_toks[1:]
        return True

    def build(path: str, lower: bool) -> tuple[dict, set]:
        d, first = {}, set()
        for alias, qids in json.load((ENT / path).open(encoding="utf-8")).items():
            k = key_of(alias, lower)
            if not k or len(k.split()) > MAX_ALIAS_TOKENS:
                continue
            good = {q for q in qids if ok(k, q)}
            if good:
                d.setdefault(k, set()).update(good)
                first.add(k.split()[0])
        return {k: sorted(v) for k, v in d.items()}, first

    ci, ci_first = build("aliases.json", True)
    cs, cs_first = build("aliases_cs.json", False)

    holders = defaultdict(list)  # (target_qid, role) -> [(start, end, person)]
    terms = defaultdict(list)    # person -> [(start, end)]
    for r in read("positions.csv"):
        span = (r["start"] or "0000", r["end"] or "9999")
        holders[(r["target"], r["role"])].append((*span, r["person"]))
        terms[r["person"]].append(span)

    R.update(etype=etype, ename=ename, ci=ci, ci_first=ci_first, cs=cs, cs_first=cs_first,
             holders=holders, terms=terms)


# ---------------------------------------------------------------- role mentions
ROLE_VI = [  # (regex right BEFORE the country/org mention, role)
    (r"(?<!Phó )(?:Tổng thống|Chủ tịch nước|Quốc vương|Nhà vua|Nữ hoàng)", "head_of_state"),
    (r"(?<!Phó )(?:Thủ tướng)", "head_of_government"),
    (r"(?:Ngoại trưởng|Bộ trưởng Ngoại giao|Bộ trưởng Bộ Ngoại giao)", "foreign_minister"),
    (r"(?:Bộ trưởng Quốc phòng|Bộ trưởng Bộ Quốc phòng)", "defence_minister"),
    (r"(?:Phó Tổng thống)", "vice_president"),
    (r"(?:Tổng thư ký|Chủ tịch|Tổng giám đốc)", "org_head"),
]
ROLE_EN_AFTER = [  # regex right AFTER the mention: "US president", "Ukraine's foreign minister"
    (r"(?:'s)?\s+(?:president|king|queen|emperor|head of state)\b", "head_of_state"),
    (r"(?:'s)?\s+(?:prime minister|premier|chancellor)\b", "head_of_government"),
    (r"(?:'s)?\s+(?:foreign minister|foreign secretary|secretary of state|top diplomat)\b", "foreign_minister"),
    (r"(?:'s)?\s+(?:defen[cs]e minister|defen[cs]e secretary|secretary of defen[cs]e|minister of defen[cs]e)\b", "defence_minister"),
    (r"(?:'s)?\s+vice[- ]president\b", "vice_president"),
    (r"(?:'s)?\s+(?:secretary[- ]general|secretary general|chief|director[- ]general|president)\b", "org_head"),
]
ROLE_EN_BEFORE = [  # "president of Brazil"
    (r"\b(?:president|king|queen) of (?:the )?$", "head_of_state"),
    (r"\b(?:prime minister|premier|chancellor) of (?:the )?$", "head_of_government"),
    (r"\bforeign minister of (?:the )?$", "foreign_minister"),
    (r"\bdefen[cs]e minister of (?:the )?$", "defence_minister"),
    (r"\bvice[- ]president of (?:the )?$", "vice_president"),
    (r"\b(?:secretary[- ]general|director[- ]general|head|chief) of (?:the )?$", "org_head"),
]
_RX_VI = [(re.compile(p + r"\s+$"), role) for p, role in ROLE_VI]
_RX_EN_A = [(re.compile(r"^" + p, re.IGNORECASE), role) for p, role in ROLE_EN_AFTER]
_RX_EN_B = [(re.compile(p, re.IGNORECASE), role) for p, role in ROLE_EN_BEFORE]


def holder(target: str, role: str, date: str) -> str | None:
    """Office holder on `date`: the most recent term start <= date that has not ended.
    Terms without a start date are ignored and open-ended old terms are shadowed by newer ones
    (Wikidata has e.g. 17th-century office holders with no end date)."""
    if not date:
        return None
    cands = [(start, person) for start, end, person in R["holders"].get((target, role), [])
             if start != "0000" and start <= date <= end]
    return max(cands)[1] if cands else None


# ---------------------------------------------------------------- linking
def match_dict(sent: str, local: dict[str, str], lang: str = "en") -> list[dict]:
    """Greedy longest-match of alias n-grams over the sentence tokens."""
    toks = tokens(sent)
    low = [vn_norm(t.group()).lower() for t in toks]
    org = [vn_norm(t.group()) for t in toks]
    out, i = [], 0
    while i < len(toks):
        hit = None
        if low[i] in R["ci_first"] or org[i] in R["cs_first"] or org[i] in local:
            for n in range(min(MAX_ALIAS_TOKENS, len(toks) - i), 0, -1):
                k_low, k_org = " ".join(low[i:i + n]), " ".join(org[i:i + n])
                qids = None
                # proper-noun check: a case-insensitive match must contain a capital letter
                if len(k_low) > 4 and any(c.isupper() for c in k_org):
                    qids = R["ci"].get(k_low)
                qids = qids or R["cs"].get(k_org)
                via = "alias"
                if not qids and n == 1 and k_org in local:
                    is_ascii = k_org.isascii()
                    if lang == "en" or is_ascii or VI_HONORIFIC.search(sent[:toks[i].start()]):
                        qids, via = [local[k_org]], "surname"
                if qids:
                    hit = (n, qids, via)
                    break
        if hit:
            n, qids, via = hit
            s, e = toks[i].start(), toks[i + n - 1].end()
            out.append({"text": sent[s:e], "start": s, "end": e, "qids": qids, "via": via})
            i += n
        else:
            i += 1
    return out


def disambiguate(m: dict, date: str) -> str:
    """Several QIDs for one alias: prefer a person in office on the article date."""
    if len(m["qids"]) == 1:
        return m["qids"][0]
    for q in m["qids"]:
        if any(start <= (date or "9999") <= end for start, end in R["terms"].get(q, [])):
            return q
    return m["qids"][0]


def add_role_mentions(sent: str, ments: list[dict], lang: str, date: str) -> None:
    """Turn 'Tổng thống Mỹ' / 'US president' into the office holder at `date`."""
    new = []
    for m in ments:
        q = m["qid"]
        typ = R["etype"].get(q)
        if typ not in ("Country", "Organization"):
            continue
        cands = []
        if lang == "vi":
            for rx, role in _RX_VI:
                mm = rx.search(sent[max(0, m["start"] - 40):m["start"]])
                if mm:
                    cands.append((role, m["start"] - (len(sent[max(0, m["start"] - 40):m["start"]]) - mm.start()), m["end"]))
        else:
            for rx, role in _RX_EN_A:
                mm = rx.match(sent[m["end"]:m["end"] + 40])
                if mm:
                    cands.append((role, m["start"], m["end"] + mm.end()))
            for rx, role in _RX_EN_B:
                mm = rx.search(sent[max(0, m["start"] - 40):m["start"]])
                if mm:
                    cands.append((role, m["start"] - (len(sent[max(0, m["start"] - 40):m["start"]]) - mm.start()), m["end"]))
        for role, s, e in cands:
            if (role == "org_head") != (typ == "Organization"):
                continue
            # skip if the person is named right after: "Tổng thống Mỹ Donald Trump"
            if any(o["start"] >= e and o["start"] - e <= 3 and R["etype"].get(o["qid"]) == "Person" for o in ments):
                continue
            p = holder(q, role, date)
            if p:
                new.append({"text": sent[s:e], "start": s, "end": e, "qid": p, "via": f"role:{role}"})
                break
    ments.extend(new)


# ---------------------------------------------------------------- sentence split
_nlp_en = None


def split_sentences(text: str, lang: str) -> list[str]:
    global _nlp_en
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    if lang == "vi":
        from underthesea import sent_tokenize
        return [s for p in paras for s in sent_tokenize(p)]
    if _nlp_en is None:
        import spacy
        _nlp_en = spacy.blank("en")
        _nlp_en.add_pipe("sentencizer")
        _nlp_en.max_length = 2_000_000
    return [s.text.strip() for p in paras for s in _nlp_en(p).sents]


# ---------------------------------------------------------------- per article
def process_article(art: dict) -> tuple[list[dict], dict]:
    lang, date = art["lang"], art.get("date", "")
    text = vn_norm(art["title"] + "\n" + art["text"])
    sents = [s for s in split_sentences(text, lang) if MIN_SENT_CHARS <= len(s) <= MAX_SENT_CHARS]

    # pass 1: find full-name persons to build article-local surname aliases
    local: dict[str, str] = {}
    first_pass = [match_dict(s, {}, lang) for s in sents]
    for ments in first_pass:
        for m in ments:
            q = disambiguate(m, date)
            if R["etype"].get(q) == "Person":
                parts = m["text"].split()
                if len(parts) >= 2 and len(parts[-1]) >= 3 and parts[-1][0].isupper():
                    local.setdefault(parts[-1], q)

    out, stats = [], Counter(sentences=len(sents))
    for idx, s in enumerate(sents):
        ments = match_dict(s, local, lang) if local else first_pass[idx]
        for m in ments:
            m["qid"] = disambiguate(m, date)
            del m["qids"]
        add_role_mentions(s, ments, lang, date)
        qids = {m["qid"] for m in ments}
        stats["mentions"] += len(ments)
        if len(qids) < 2:
            continue
        stats["sent_2ent"] += 1
        trig = find_triggers(s, lang)
        if not trig:
            continue
        ments.sort(key=lambda m: m["start"])
        out.append({
            "sid": f"{hashlib.md5(art['url'].encode()).hexdigest()[:10]}-{idx}",
            "url": art["url"], "source": art["source"], "lang": lang, "date": date,
            "sentence": s,
            "entities": [{"qid": m["qid"], "name": R["ename"].get(m["qid"], ""), "type": R["etype"].get(m["qid"], ""),
                          "text": m["text"], "start": m["start"], "end": m["end"], "via": m["via"]} for m in ments],
            "triggers": trig,
        })
    stats["kept"] = len(out)
    return out, dict(stats)


# ---------------------------------------------------------------- main
def iter_articles(sources: list[str], limit: int | None):
    seen_urls, seen_titles = set(), set()
    dup = 0
    for src in sources:
        path = RAW / f"{src}.jsonl"
        if not path.exists():
            print(f"  (missing {path.name})")
            continue
        n = 0
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    a = json.loads(line)
                except json.JSONDecodeError:
                    continue
                tkey = re.sub(r"\W+", "", vn_norm(a.get("title", "")).lower())
                if a["url"] in seen_urls or (tkey and tkey in seen_titles):
                    dup += 1
                    continue
                seen_urls.add(a["url"])
                seen_titles.add(tkey)
                yield a
                n += 1
                if limit and n >= limit:
                    break
    iter_articles.duplicates = dup


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sources", nargs="+", default=["guardian", "vnexpress", "baoquocte"])
    ap.add_argument("--limit", type=int, help="max articles per source (for quick tests)")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    arts = list(iter_articles(args.sources, args.limit))
    print(f"{len(arts)} unique articles ({iter_articles.duplicates} duplicates removed)")
    if not arts:  # never overwrite sentences.jsonl / entity_links.jsonl.gz with an empty result
        raise SystemExit(f"no articles found in {RAW} - crawl first (README step 2)")

    total, per_src = Counter(), defaultdict(Counter)
    ent_count, trig_count, via_count, type_pairs = Counter(), Counter(), Counter(), Counter()
    seen_sent = set()
    out_path = OUT / "sentences.jsonl"
    with out_path.open("w", encoding="utf-8") as f, Pool(args.workers, initializer=load_resources) as pool:
        for (rows, st), art in zip(tqdm(pool.imap(process_article, arts, chunksize=20), total=len(arts)), arts):
            total.update(st)
            per_src[art["source"]].update(st)
            per_src[art["source"]]["articles"] += 1
            for r in rows:
                h = hashlib.md5(r["sentence"].encode()).hexdigest()
                if h in seen_sent:
                    total["dup_sentences"] += 1
                    continue
                seen_sent.add(h)
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                total["written"] += 1
                trig_count.update(r["triggers"])
                for e in r["entities"]:
                    ent_count[(e["qid"], e["name"], e["type"])] += 1
                    via_count[e["via"].split(":")[0]] += 1
                type_pairs["-".join(sorted({e["type"] for e in r["entities"]}))] += 1

    stats = {
        "articles": len(arts), "duplicate_articles": iter_articles.duplicates, **total,
        "per_source": {k: dict(v) for k, v in per_src.items()},
        "distinct_entities": len(ent_count),
        "distinct_by_type": dict(Counter(t for (_, _, t) in ent_count)),
        "mentions_by_method": dict(via_count),
        "triggers": dict(trig_count.most_common()),
        "entity_type_combinations": dict(type_pairs.most_common(10)),
        "top_entities": [{"qid": q, "name": n, "type": t, "sentences": c} for (q, n, t), c in ent_count.most_common(30)],
    }
    (OUT / "preprocess_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in stats.items() if k not in ("top_entities",)}, ensure_ascii=False, indent=1))
    print("top:", ", ".join(f"{e['name']} ({e['sentences']})" for e in stats["top_entities"][:15]))
    from build_graph import LINKS, write_links  # compact copy of the entity links, committed to git
    write_links()
    print(f"-> {out_path.relative_to(ROOT)}, {LINKS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
