# PoliticsNetwork

**A social network of Heads of State and International Organizations, extracted from diplomatic news with NLP.**

The project crawls the *World / International* sections of Vietnamese and English online newspapers (e.g. *Báo Thế giới & Việt Nam*, VnExpress, The Guardian), extracts diplomatic actions between presidents, prime ministers, governments and international organizations (UN, NATO, EU, ASEAN, ...), and stores the result as a property graph in **Neo4j**.

Target: ≥ 2,000 nodes, ≥ 5,000 edges, ≥ 10 relation types, ≥ 90% precision.

---

## Tech stack

| Layer | Tools |
|---|---|
| Crawling | `requests`, `trafilatura`, Guardian Open Platform API |
| NLP (Vietnamese) | `underthesea` (sentence split, NER) |
| NLP (English) | `spaCy` (rule-based sentencizer, no model download needed) |
| Entity linking | Wikidata SPARQL (`requests`) alias dictionary, exact longest-match |
| Relation extraction | Keyword rules + LLM via **[9router](https://9router.com/)** (OpenAI-compatible gateway, `openai` SDK) |
| Agent / orchestration | **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** (optional, pointed at the same 9router endpoint) |
| Graph database | **Neo4j 5.26** + APOC + Graph Data Science (Docker) |
| Analysis | `networkx`, `matplotlib`, Neo4j GDS |


---

## Project structure

```
PoliticsNetwork/
├── cypher/
│   ├── 00_schema.cypher     # constraints & indexes
│   └── 01_queries.cypher    # example analysis queries (demo)
├── data/
│   ├── raw/                 # crawled articles (git-ignored, ~600 MB)
│   ├── processed/
│   │   ├── sentences.jsonl        # candidate sentences + linked entities (git-ignored, rebuilt by step 3)
│   │   ├── entity_links.jsonl.gz  # compact entity links (committed; used by build_graph in a fresh clone)
│   │   ├── relations.jsonl        # LLM extraction output (committed: not cheaply re-creatable)
│   │   └── preprocess_stats.json
│   ├── entities/            # Wikidata seeds & alias dictionary (committed: Wikidata changes over time)
│   └── graph/               # nodes / edges (.csv, .json) (mounted to Neo4j /import); network.graphml (git-ignored)
├── reports/                 # stats, evaluation samples + human labels, annotation guide (figures/ git-ignored)
├── src/
│   ├── check_env.py         # environment check (Neo4j + 9router)
│   ├── seed_wikidata.py     # step 1: entities, positions, memberships, aliases
│   ├── crawl/               # step 2: guardian.py, vnexpress.py, baoquocte.py, common.py
│   ├── preprocess.py        # step 3: sentence split, entity linking (uses lexicon.py)
│   ├── extract_rel.py       # step 4: LLM ensemble relation extraction
│   ├── build_graph.py       # step 5: aggregate edges, QC rules, KB enrichment
│   ├── load_neo4j.py        # step 5: load graph into Neo4j
│   ├── stats.py             # step 6: network statistics and figures
│   └── evaluate.py          # step 7: sampling, scoring (precision, kappa)
├── docker-compose.yml       # Neo4j 5.26 + APOC + GDS
├── environment.yml          # conda environment
├── requirements.txt         # pip dependencies
└── .env.example             # environment variable template
```
---

## Graph model

### Nodes

| Label | Key properties | Description |
|---|---|---|
| `Person` | `qid`, `name`, `name_vi`, `role` | Presidents, prime ministers, foreign ministers, heads of organizations |
| `Country` | `qid`, `name`, `name_vi`, `iso3` | Sovereign states |
| `Organization` | `qid`, `name`, `name_vi`, `acronym` | UN, NATO, EU, ASEAN, G7, IMF, ... |
| `Agreement` | `name`, `date`, `type` | Treaties, MoUs, joint statements |
| `Article` | `url`, `title`, `date`, `source`, `lang` | Source news article (evidence) |

All entity nodes also carry the `Entity` label; `qid` is the Wikidata ID and is unique.

### Relationships

| Type | Group | Meaning |
|---|---|---|
| `MET_WITH` | Talks | Held talks / met |
| `PHONE_CALL` | Talks | Phone call |
| `VISITED` | Talks | Official / state visit |
| `SIGNED_AGREEMENT` | Strategic signing | Signed an agreement / MoU |
| `UPGRADED_PARTNERSHIP` | Strategic signing | Upgraded bilateral relations |
| `MILITARY_AID` | Aid | Provided military aid |
| `ECONOMIC_AID` | Aid | Provided economic aid, loans, grants |
| `SANCTIONED` | Sanction | Imposed sanctions |
| `EXPELLED_DIPLOMAT` | Expulsion | Expelled diplomats |
| `CONDEMNED` | Attitude (−) | Condemned / criticized |
| `SUPPORTED` | Attitude (+) | Supported / welcomed |
| `LEADER_OF` | Structural | Person → Country / Organization |
| `MEMBER_OF` | Structural | Country → Organization |

Every extracted edge stores `date`, `article_url`, `sentence` (evidence), `confidence` and `weight` (number of mentions).

---

## Setup

### 1. Prerequisites

- **Git**
- **Miniconda / Anaconda** (Python is managed by conda)
- **Docker Desktop** (≈ 4 GB RAM free for Neo4j)
- **Node.js ≥ 18** + **9router** — `npm install -g 9router`
- *(optional)* **Hermes Agent**

### 2. Clone the repository

```powershell
git clone https://github.com/NguyenXTKhai/PoliticsNetwork.git
cd PoliticsNetwork
```

### 3. Create the conda environment

```powershell
conda env create -f environment.yml
conda activate politics-network
```

To add a package later: `pip install <pkg>` and append it to `requirements.txt`.

**Windows console & Vietnamese text:** run `chcp 65001` and `$env:PYTHONIOENCODING="utf-8"` to avoid garbled output.

### 4. Configure environment variables

```powershell
Copy-Item .env.example .env
```

Edit `.env`:

| Variable | Value |
|---|---|
| `NEO4J_PASSWORD` | Password for the `neo4j` user (used on first start of the container) |
| `LLM_BASE_URL` | `http://localhost:20128/v1` (9router) |
| `LLM_API_KEY` | API key from the 9router dashboard |
| `LLM_MODEL` | Model or combo name in 9router (e.g. `nlp-extract`) |
| `GUARDIAN_API_KEY` | Free key from <https://open-platform.theguardian.com/access/> |


### 5. Start 9router and create an extraction combo

```powershell
9router            # dashboard: http://localhost:20128
```

In the dashboard:

1. Connect at least one provider (Gemini, OpenRouter, ...).
2. Create a **combo** with an ordered fallback list (e.g. a fast Gemini Flash model first, free OpenRouter models as fallback).
3. Copy the API key into `.env`.

Tip: send 10–20 sentences per request to stay within free-tier rate limits.

*(Optional)* Point Hermes Agent at the same gateway — in Hermes `config.yaml`: `provider: custom`, `base_url: http://localhost:20128/v1`. Use Hermes for orchestration (writing/debugging crawlers, scheduling crawls, running Cypher), and the Python pipeline for bulk extraction so results are reproducible.

### 6. Start Neo4j

```powershell
docker compose up -d
docker logs -f neo4j-diplomacy      # wait for "Started." then Ctrl+C
```

- The **first start can take 2–4 minutes** (downloading the GDS plugin and creating the store on a Windows bind mount). Later starts are faster.
- Warnings such as `GDS license state: unlicensed` and the Log4j `package scanning` notice are harmless — the free GDS edition includes all algorithms needed.
- Data persists in `./neo4j/` (git-ignored). `data/graph/` is mounted as `/import` for `LOAD CSV`.

Neo4j Browser: <http://localhost:7474> — user `neo4j`, password from `.env`.

Create constraints and indexes:

```powershell
Get-Content cypher/00_schema.cypher | docker exec -i neo4j-diplomacy cypher-shell -u neo4j -p <password>
```

### 7. Verify the environment

```powershell
python src/check_env.py
```

Expected output:

```
[OK] Neo4j   | APOC 5.26.x | GDS 2.13.x | 0 nodes
[OK] 9router | <model> | {"relations": [{"source": "Phạm Minh Chính", "target": "Lee Jae-myung", "relation": "MET_WITH"}, ...]}
```

---

## Daily commands

```powershell
conda activate politics-network
docker compose up -d        # start Neo4j
docker compose stop         # stop Neo4j (data is kept)
9router -n -t               # run 9router in the tray without opening the browser
```

---

## Data pipeline

Run all commands from the project root with the conda env active.

### Step 1 — Seed entities from Wikidata (~2 min)

```powershell
python src/seed_wikidata.py              # office terms ending in/after 2000
python src/seed_wikidata.py --since 2010 # narrower
```

Writes `data/entities/`: `persons.csv` (~5,200 heads of state/government, foreign ministers, heads of organizations), `countries.csv` (197), `organizations.csv` (~95), `positions.csv` (→ `LEADER_OF`), `memberships.csv` (→ `MEMBER_OF`), and two alias dictionaries:

- `aliases.json` — names longer than 4 characters, matched case-insensitively (`"tập cận bình"` → Xi Jinping)
- `aliases_cs.json` — short names / acronyms, matched case-sensitively (`"Anh"`, `"Mỹ"`, `"NATO"`, `"LHQ"`), because many collide with ordinary Vietnamese words (`nam`, `anh`, `đức`)

Vietnamese press names are added in `data/entities/manual_aliases.csv` (`alias,enwiki` — English Wikipedia title, resolved to a QID at runtime).

### Step 2 — Crawl news articles

| Source | Command | Method | Volume (since 2024) |
|---|---|---|---|
| The Guardian (EN) | `python -m src.crawl.guardian --from 2024-01-01` | Open Platform API, `section=world`, diplomacy query | ~15,100 articles, ~6 min |
| VnExpress — Thế giới (VI) | `python -m src.crawl.vnexpress --since 2024-01` | Weekly date-range archive (max ~20 pages per query) | ~50 diplomatic articles / week |
| Báo Thế giới & Việt Nam (VI) | `python -m src.crawl.baoquocte --since 2024-01` | Monthly sitemaps (category pages use `/ajax/`, disallowed by robots.txt) | ~950 diplomatic articles / month |

- Vietnamese URLs are pre-filtered by diplomatic slug keywords (`tong-thong`, `hoi-dam`, `trung-phat`, `vien-tro`, `nato`, ...); add `--all` to disable.
- Polite crawling: 3 worker threads (`--workers`), ~1 s delay per request, retries with exponential backoff.
- Crawls are **resumable**: each source is appended to `data/raw/<source>.jsonl` and already-saved URLs are skipped, so just re-run the same command after an interruption.
- baoquocte.vn currently publishes no monthly sitemaps for 2025.
- Guardian free key: 500 calls/day × 200 articles/call.

Record format (one JSON object per line):

```json
{"url": "...", "title": "...", "date": "2026-11-08", "source": "baoquocte", "lang": "vi",
 "section": "Toàn cảnh", "description": "...", "text": "..."}
```

Check progress:

```powershell
Get-ChildItem data/raw/*.jsonl | ForEach-Object { "$($_.Name): $((Get-Content $_ | Measure-Object -Line).Lines)" }
```

### Step 3 — Preprocessing & entity linking (~1–2 min)

```powershell
python src/preprocess.py                               # all sources, 4 worker processes
python src/preprocess.py --sources guardian --limit 200  # quick test
```

1. **De-duplication** — articles by URL and normalized title, sentences by exact text (wire copies).
2. **Normalization** — Unicode NFC and a single Vietnamese tone-mark style (`hoà` → `hòa`, `uý` → `úy`) applied to both text and aliases.
3. **Sentence splitting** — spaCy `sentencizer` (EN), `underthesea.sent_tokenize` (VI); sentences of 30–800 characters.
4. **Entity linking** (dictionary-based, longest match first):
   - long aliases case-insensitively, but the matched span must contain a capital letter (proper noun);
   - short aliases case-sensitively (`Anh`, `Mỹ`, `NATO`);
   - generic titles are stop-listed (`prime minister`, `security council`, `chính phủ`);
   - one-word person aliases must be a surname (`Putin`), never a given name (`David`);
   - **article-level surnames**: after "Emmanuel Macron" appears, "Macron" links to him; in Vietnamese only after an honorific (`ông`, `Thủ tướng`, ...) so that "**Chính** phủ" is not linked to Phạm Minh Chính;
   - **role mentions** resolved by article date: "Tổng thống Philippines", "the UK prime minister", "Tổng thư ký LHQ" → the office holder whose most recent term started before the article date;
   - ambiguous aliases → the candidate in office on the article date.
5. **Filtering** — keep sentences with ≥ 2 distinct entities **and** ≥ 1 diplomatic trigger word from `src/lexicon.py` (11 relation types, VI + EN).

Output `data/processed/sentences.jsonl` (git-ignored, large) + `data/processed/preprocess_stats.json`:

```json
{"sid": "…-12", "url": "…", "source": "baoquocte", "lang": "vi", "date": "2024-01-29",
 "sentence": "Tổng thống Thổ Nhĩ Kỳ Tayyip Erdogan ngày 25/1 đã ký phê duyệt nghị định thư xin gia nhập NATO của Thụy Điển.",
 "entities": [{"qid": "Q43", "name": "Turkey", "type": "Country", "text": "Thổ Nhĩ Kỳ", "start": 11, "end": 21, "via": "alias"}, …],
 "triggers": ["SIGNED_AGREEMENT"]}
```

Known data issues handled: fictional office holders in Wikidata (filtered by `P31 = Q5`), open-ended 17th-century terms (resolved by latest term start).


### Step 4 — Relation extraction with an LLM ensemble (resumable)

```powershell
python src/extract_rel.py --workers 4 `
    --ensemble agnes/agnes-2.5-flash+agnes/agnes-3.0-flash `
    --ensemble oc/deepseek-v4.1-flash-free+oc/glm-5.3-flash-free
```

- Sentences are sent in batches of 15 with the candidate entity pairs; the model answers JSON
  (relation, source, target, confidence, agreement / event name).
- **Ensemble agreement**: a relation is kept only when *all* models of an ensemble return it
  (same type, endpoints, direction). **Cascade**: model 2 only re-checks sentences where model 1 found something.
- **Rule validation**: schema type constraints (e.g. `MILITARY_AID` source must be a Country / Organization),
  no self-loops, a person is never related to the state he/she represents, confidence ≥ 0.7.
- Several `--ensemble` options share one queue (one per provider, so rate limits do not add up).
  A provider that runs out of its daily quota retires and its batches go back to the queue.
- **Coverage-first order**: sentences that mention entities not yet in the graph are processed first
  (round-robin over entities); sentences re-linked after a dictionary update are re-extracted.
- Output `data/processed/relations.jsonl` (append-only; the latest record per sentence wins),
  call log `data/processed/llm_calls.log`. Re-running continues where it stopped.

### Step 5 — Build the graph and load Neo4j

```powershell
python src/build_graph.py              # -> data/graph/nodes|edges.(json|csv)
python src/load_neo4j.py               # wipes and loads; --keep to MERGE
```

- Diplomatic edges aggregated per (source, target, type): weight = supporting sentences, first/last date,
  languages, sources, up to 3 evidence sentences with URLs. Post-hoc QC rules (`QC_REQUIRE`) drop
  relations whose sentence lacks the required cue (e.g. a "summoned the ambassador" EXPELLED_DIPLOMAT).
- Agreement / Event nodes from the names the LLM extracted (`PARTY_TO`, `ATTENDED`).
- Structural edges from Wikidata between graph nodes: `LEADER_OF`, `AMBASSADOR_OF`, `MEMBER_OF`.
- **KB enrichment** (`--kb-min-mentions 3`, 0 = off): persons linked by full name in ≥ 3 news sentences
  who hold a Wikidata position at a graph node are added with that edge (`origin = "kb"`, others `"news"`).

### Step 6 — Network statistics

```powershell
python src/stats.py    # -> reports/stats.json, stats.md, figures/*.png, data/graph/network.graphml (Gephi)
```

Density, degree, components, clustering, assortativity, sampled shortest paths, PageRank (diplomatic edges),
betweenness, Louvain communities and modularity.

### Step 7 — Precision evaluation (manual)

```powershell
python src/evaluate.py sample --n 300       # stratified by relation type -> reports/eval_sample.csv
python src/evaluate.py sample-kb --n 60     # KB-enriched nodes          -> reports/eval_kb_sample.csv
# two annotators fill label_a / label_b following reports/ANNOTATION_GUIDE.md, then:
python src/evaluate.py score
python src/evaluate.py score --file reports/eval_kb_sample.csv
```

Precision with a Wilson 95% interval, Cohen's kappa, and breakdowns per relation type, language and ensemble.

> **Do not re-run `sample` / `sample-kb`** once labels exist: they overwrite `eval_sample.csv` /
> `eval_kb_sample.csv` (the human labels). Kappa is computed from the original blind labels in
> `reports/eval_subset_B.csv` (annotator B); `label_b` in `eval_sample.csv` holds the adjudicated values.

Example Cypher queries for the demo: `cypher/01_queries.cypher`.

## Reproducing the reported results (fresh clone)

The repository freezes every non-deterministic input, so the graph and all numbers can be rebuilt on another
machine **without crawling and without any LLM / API call**:

| Committed file | Freezes |
|---|---|
| `data/entities/` | Wikidata snapshot (+ manual label fixes) |
| `data/processed/relations.jsonl` | LLM extraction output |
| `data/processed/entity_links.jsonl.gz` | entity links per sentence (compact copy of the git-ignored `sentences.jsonl`) |
| `reports/eval_*.csv` | human annotation labels |

```powershell
conda env create -f environment.yml; conda activate politics-network
copy .env.example .env                   # set NEO4J_PASSWORD (LLM / Guardian keys are not needed)
docker compose up -d                     # Neo4j
python src/build_graph.py                # -> data/graph/ (2130 nodes, 11644 edges, 16 types; identical files)
python src/load_neo4j.py
python src/stats.py                      # -> reports/stats.*, reports/figures/, data/graph/network.graphml
python src/evaluate.py score; python src/evaluate.py score --file reports/eval_kb_sample.csv
```

What a clone can **not** do without extra data: re-run `preprocess.py` / `extract_rel.py` (they need the
crawled articles `data/raw/` and full sentences, ~800 MB, not in git; re-crawl with step 2, which may return a
slightly different article set) or `evaluate.py sample-kb` (needs sentence text). `preprocess.py` refuses to
run without `data/raw/`, so it cannot overwrite the committed files by accident.

Do **not** re-run `seed_wikidata.py` (it would fetch a newer Wikidata snapshot, so entity ids,
positions and aliases may differ) or `extract_rel.py` from scratch unless you intend to change the results.
