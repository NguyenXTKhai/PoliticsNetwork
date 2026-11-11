# PoliticsNetwork

**A social network of Heads of State and International Organizations, extracted from diplomatic news with NLP.**

The project crawls the *World / International* sections of English and Vietnamese online newspapers
(The Guardian, VnExpress, *Báo Thế giới & Việt Nam*), links mentions of leaders, countries and international
organizations to Wikidata, extracts diplomatic actions between them with an LLM ensemble, and stores the
result as a property graph in **Neo4j**.

## Results

| | Requirement | Result |
|---|---|---|
| Nodes | ≥ 2,000 | **2,130** (1,822 extracted from news, 308 added from Wikidata) |
| Edges | ≥ 5,000 | **11,644** |
| Relation types | ≥ 10 | **16** (11 extracted from text + 5 structural) |
| Precision | > 90% | **92.7%** raw (Wilson 95% CI 89.2–95.1%), **93.4%** weighted by relation type (CI 90.8–96.0%), on 314 manually labelled relations |

- Data: 83,232 articles (Guardian 56,395 from 2016 · baoquocte 19,301 and VnExpress 7,536 from 2024), up to Nov 2026,
  239,183 candidate sentences, 65,715 sentences processed by the LLM.
- Inter-annotator agreement on a blind double-annotated subset of 108: 83.3% raw, Cohen's κ = 0.26
  (low because ~87% of the labels are positive - the kappa paradox).
- Nodes added from Wikidata (KB enrichment): 96.7% correct on 60 sampled nodes.
- Network: one connected component, average degree 9.1, average clustering 0.51, 10 Louvain communities
  (modularity 0.40). Full statistics: [reports/stats.md](reports/stats.md).

---

## Tech stack

| Layer | Tools |
|---|---|
| Crawling | `requests`, `trafilatura`, Guardian Open Platform API, sitemaps / archive pages |
| Sentence splitting | `underthesea` (Vietnamese), `spaCy` rule-based sentencizer (English, no model download) |
| Entity linking | Wikidata SPARQL (via `requests`) → bilingual alias dictionary, longest-match, date-aware disambiguation |
| Relation extraction | Trigger lexicon pre-filter + LLM ensemble (2 models must agree) via **[9router](https://9router.com/)** (OpenAI-compatible gateway, `openai` SDK) + rule validation |
| Graph database | **Neo4j 5.26** + APOC + Graph Data Science (Docker) |
| Analysis | `networkx` (+ `scipy`), `matplotlib`, Neo4j GDS |

---

## Project structure

```
PoliticsNetwork/
├── cypher/
│   ├── 00_schema.cypher           # constraint & name indexes (optional, load_neo4j creates the constraint)
│   └── 01_queries.cypher          # example analysis queries (demo)
├── data/
│   ├── raw/                       # crawled articles (git-ignored, ~600 MB)
│   ├── entities/                  # Wikidata snapshot: persons, countries, organizations, positions,
│   │                              #   memberships, alias dictionaries (committed)
│   ├── processed/
│   │   ├── sentences.jsonl        # candidate sentences + linked entities (git-ignored, ~200 MB)
│   │   ├── entity_links.jsonl.gz  # compact copy of the entity links (committed; used in a fresh clone)
│   │   ├── relations.jsonl        # LLM extraction output (committed)
│   │   └── preprocess_stats.json
│   └── graph/                     # nodes / edges (.json, .csv), network.graphml (git-ignored)
├── reports/
│   ├── stats.json, stats.md, figures/      # network statistics
│   ├── ANNOTATION_GUIDE.md                 # labelling rules for the precision evaluation
│   ├── eval_sample.csv, eval_kb_sample.csv # evaluation samples with the final labels
│   ├── eval_subset_B.csv                   # blind labels of annotator B (used for kappa)
│   ├── eval_*_claude.csv                   # LLM-assisted pre-labels (source of label A, for transparency)
│   └── eval_strata.json, eval_*result.json # stratum sizes, scores
├── src/
│   ├── check_env.py               # environment check (Neo4j + 9router)
│   ├── seed_wikidata.py           # step 1: entities, positions, memberships, aliases
│   ├── crawl/                     # step 2: guardian.py, vnexpress.py, baoquocte.py, common.py
│   ├── preprocess.py              # step 3: sentence split, entity linking (uses lexicon.py)
│   ├── lexicon.py                 # relation types and trigger words (VI + EN)
│   ├── extract_rel.py             # step 4: LLM ensemble relation extraction
│   ├── build_graph.py             # step 5: aggregate edges, QC rules, Agreement/Event nodes, KB enrichment
│   ├── load_neo4j.py              # step 5: load the graph into Neo4j
│   ├── stats.py                   # step 6: network statistics and figures
│   └── evaluate.py                # step 7: sampling and scoring (precision, kappa)
├── docker-compose.yml             # Neo4j 5.26 + APOC + GDS
├── environment.yml                # conda environment (Python 3.12 + requirements.txt)
├── requirements.txt               # pinned pip dependencies
└── .env.example                   # environment variable template
```

---

## Graph model

### Nodes

Every node has the label `Entity` plus one type label. `id` is unique.

| Label | Count | `id` | Description |
|---|---|---|---|
| `Person` | 1,238 | Wikidata QID | Heads of state / government, ministers, ambassadors, heads of organizations |
| `Country` | 182 | Wikidata QID | Sovereign states |
| `Organization` | 80 | Wikidata QID | UN, NATO, EU, ASEAN, G7, WHO, ... |
| `Agreement` | 355 | `agr:<slug>` | Treaties, deals, MoUs named in the news |
| `Event` | 275 | `evt:<slug>` | Summits, meetings, forums named in the news |

Properties: `name`, `name_vi`, `description`, `iso3` (countries), `degree`, `first_date` (Agreement / Event),
`origin` (`news` = extracted from text, `kb` = added from Wikidata because the person is mentioned in the news).

### Relationships

| Type | Count | Source | Meaning |
|---|---|---|---|
| `MET_WITH` | 2,021 | text (symmetric) | held talks / met |
| `PHONE_CALL` | 546 | text (symmetric) | phone or video call |
| `VISITED` | 918 | text | official / state visit |
| `SIGNED_AGREEMENT` | 497 | text (symmetric) | signed an agreement, treaty, MoU, joint statement |
| `UPGRADED_PARTNERSHIP` | 101 | text (symmetric) | established / upgraded relations or partnership |
| `MILITARY_AID` | 108 | text | provided or pledged weapons / military aid |
| `ECONOMIC_AID` | 211 | text | provided or pledged economic / humanitarian aid, loans |
| `SANCTIONED` | 226 | text | imposed sanctions / embargo |
| `EXPELLED_DIPLOMAT` | 97 | text | expelled diplomats, recalled ambassador, cut ties |
| `CONDEMNED` | 993 | text | condemned, criticised, accused, threatened |
| `SUPPORTED` | 721 | text | supported, praised, welcomed |
| `PARTY_TO` | 803 | text | party to an `Agreement` node |
| `ATTENDED` | 792 | text | attended an `Event` node |
| `LEADER_OF` | 1,604 | Wikidata | Person → Country / Organization (one edge per position title; `terms` list) |
| `AMBASSADOR_OF` | 111 | Wikidata | Person → Country |
| `MEMBER_OF` | 1,895 | Wikidata | Country → Organization |

Edge properties: `weight` (number of supporting sentences, or number of terms for `LEADER_OF`), `confidence`,
`directed`, `first_date`, `last_date`, `sources` / `langs` (JSON counts), `evidence` (JSON list of up to 3
sentences with URL and date), `example`, `example_url`; structural edges also `role`, `title`, `terms`.

---

## Setup

### 1. Prerequisites

- **Git**
- **Miniconda / Anaconda**
- **Docker Desktop** (≈ 4 GB RAM free for Neo4j)
- Only to re-run the LLM extraction: **Node.js ≥ 18** + **9router** (`npm install -g 9router`)

### 2. Clone and create the environment

```powershell
git clone https://github.com/NguyenXTKhai/PoliticsNetwork.git
cd PoliticsNetwork
conda env create -f environment.yml
conda activate politics-network
```

Without conda: Python 3.12 + `pip install -r requirements.txt`.

**Windows console & Vietnamese text:** run `chcp 65001` and `$env:PYTHONIOENCODING="utf-8"`.

### 3. Configure environment variables

```powershell
Copy-Item .env.example .env
```

| Variable | Needed for | Value |
|---|---|---|
| `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD` | Neo4j | `bolt://localhost:7687`, `neo4j`, password set on first start of the container |
| `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL` | step 4 only | 9router endpoint (`http://localhost:20128/v1`), key and default model / combo |
| `GUARDIAN_API_KEY` | step 2 only | free key from <https://open-platform.theguardian.com/access/> |

### 4. Start Neo4j

```powershell
docker compose up -d
docker logs -f neo4j-diplomacy      # wait for "Started." then Ctrl+C
```

- The first start takes 2–4 minutes (downloading the APOC / GDS plugins, needs internet).
- Warnings such as `GDS license state: unlicensed` are harmless.
- Data persists in `./neo4j/` (git-ignored). `data/graph/` is mounted as `/import`.
- Neo4j Browser: <http://localhost:7474> — user `neo4j`, password from `.env`.

### 5. (Only for step 4) Start 9router

```powershell
9router            # dashboard: http://localhost:20128
```

Connect the providers (e.g. Agnes, OpenRouter, OpenCode), copy the API key into `.env`, then check:

```powershell
python src/check_env.py     # [OK] Neo4j | APOC | GDS ...   [OK] 9router | <model> | {...}
```

---

## Quick start: rebuild the reported results (fresh clone)

The repository freezes every non-deterministic input, so the graph and all numbers can be rebuilt
**without crawling and without any LLM / API call**:

| Committed file | Freezes |
|---|---|
| `data/entities/` | Wikidata snapshot (+ manual label fixes) |
| `data/processed/relations.jsonl` | LLM extraction output |
| `data/processed/entity_links.jsonl.gz` | entity links per sentence (compact copy of the git-ignored `sentences.jsonl`) |
| `reports/eval_*.csv` | human annotation labels |

```powershell
docker compose up -d
python src/build_graph.py      # -> data/graph/ (2,130 nodes, 11,644 edges, 16 types)
python src/load_neo4j.py       # -> Neo4j (wipes and reloads)
python src/stats.py            # -> reports/stats.*, reports/figures/, data/graph/network.graphml
python src/evaluate.py score   # -> reports/eval_result.json
python src/evaluate.py score --file reports/eval_kb_sample.csv
```

Then open <http://localhost:7474> and try the queries in [cypher/01_queries.cypher](cypher/01_queries.cypher).

A fresh clone can **not** re-run `preprocess.py` / `extract_rel.py` (they need the crawled articles
`data/raw/`, ~600 MB, not in git; re-crawling may return a slightly different article set) or
`evaluate.py sample-kb` (needs the sentence text). `preprocess.py` refuses to run without `data/raw/`, so it
cannot overwrite the committed files by accident.

---

## Full data pipeline

Run all commands from the project root with the conda env active. Steps 1–4 change the results; do not
re-run them unless that is intended.

### Step 1 — Seed entities from Wikidata (~2 min)

```powershell
python src/seed_wikidata.py              # office terms ending in/after 2000
```

Writes `data/entities/`: `persons.csv` (18,169 office holders), `countries.csv` (197), `organizations.csv`
(166), `positions.csv` (23,813 → `LEADER_OF` / `AMBASSADOR_OF`), `memberships.csv` (4,721 → `MEMBER_OF`) and
two alias dictionaries:

- `aliases.json` — names longer than 4 characters, matched case-insensitively (`"tập cận bình"` → Xi Jinping)
- `aliases_cs.json` — short names / acronyms, matched case-sensitively (`"Anh"`, `"Mỹ"`, `"NATO"`, `"LHQ"`)

Vietnamese press names are added in `manual_aliases.csv` (`alias,enwiki`). Wrong (vandalised) Wikidata
labels are corrected in `LABEL_FIX` / `BAD_ALIAS` inside `seed_wikidata.py`.
Re-running fetches a newer Wikidata snapshot, so ids, positions and aliases may differ.

### Step 2 — Crawl news articles (resumable)

| Source | Command | Method |
|---|---|---|
| The Guardian (EN) | `python -m src.crawl.guardian --from 2016-01-01` | Open Platform API, `section=world`, diplomacy query |
| VnExpress — Thế giới (VI) | `python -m src.crawl.vnexpress --since 2024-01` | weekly date-range archive |
| Báo Thế giới & Việt Nam (VI) | `python -m src.crawl.baoquocte --since 2024-01` | monthly sitemaps |

- Vietnamese URLs are pre-filtered by diplomatic slug keywords (`tong-thong`, `hoi-dam`, `trung-phat`, ...);
  `--all` disables the filter.
- Polite crawling: 3 worker threads, ~1 s delay, retries with exponential backoff.
- Output `data/raw/<source>.jsonl`; already-saved URLs are skipped, so re-run the same command after an
  interruption. baoquocte.vn publishes no monthly sitemaps for 2025.

### Step 3 — Preprocessing & entity linking

```powershell
python src/preprocess.py                                 # all sources, 4 worker processes
python src/preprocess.py --sources guardian --limit 200  # quick test
```

1. **De-duplication** of articles (URL, normalized title) and sentences (exact text).
2. **Normalization** — Unicode NFC and one Vietnamese tone-mark style (`hoà` → `hòa`).
3. **Sentence splitting** — spaCy `sentencizer` (EN), `underthesea.sent_tokenize` (VI); 30–800 characters.
4. **Entity linking** (dictionary, longest match first): proper-noun check for long aliases, case-sensitive
   short aliases, stop-listed generic titles, surname-only person aliases within an article after the full
   name, role mentions ("Tổng thống Philippines", "the UK prime minister") resolved to the office holder on
   the article date, ambiguous aliases resolved by date.
5. **Filtering** — keep sentences with ≥ 2 distinct entities and ≥ 1 diplomatic trigger word
   (`src/lexicon.py`, 11 relation types, VI + EN).

Output: `sentences.jsonl`, `entity_links.jsonl.gz`, `preprocess_stats.json` in `data/processed/`.

### Step 4 — Relation extraction with LLM ensembles (resumable)

```powershell
python src/extract_rel.py --workers 4 `
    --ensemble agnes/agnes-2.5-flash+agnes/agnes-3.0-flash `
    --ensemble "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free+openrouter/nvidia/nemotron-3.5-super-vl:free" `
    --ensemble oc/deepseek-v4.1-flash-free+oc/glm-5.3-flash-free
```

- Batches of 15 sentences; each linked entity gets a local id (E1, E2, ...) so the model cannot invent nodes.
  The model answers JSON (relation, source, target, confidence, agreement / event name).
- **Ensemble agreement**: a relation is kept only when both models of an ensemble return it (same type,
  endpoints, direction); the second model only re-checks sentences where the first found something.
- **Rule validation**: type constraints (e.g. `MILITARY_AID` source is a Country / Organization), trigger
  compatibility, no self-loops, a person is never related to the state he/she represents, confidence ≥ 0.7.
- Several ensembles share one queue (one per provider); a provider that runs out of its daily quota retires.
- **Coverage-first order**: sentences mentioning entities not yet in the graph go first.
- Output `relations.jsonl` (append-only; the latest record per sentence wins), call log `llm_calls.log`.

### Step 5 — Build the graph and load Neo4j

```powershell
python src/build_graph.py              # -> data/graph/nodes|edges.(json|csv)
python src/load_neo4j.py               # wipes and loads; --keep to MERGE
```

- Text relations aggregated per (source, target, type): weight, first/last date, languages, sources,
  up to 3 evidence sentences with URLs. QC rules drop relations whose sentence lacks the required cue
  (an `EXPELLED_DIPLOMAT` needs "expel / recall / cut ties ...") or whose endpoint is no longer linked.
- Agreement / Event nodes from the names the LLM extracted: generic names ("summit", "joint statement"),
  places, aggregates ("37 agreements") and names of existing organizations are dropped; variants of one
  recurring event are merged (`Davos`, `WEF` → World Economic Forum).
- Structural edges from Wikidata between graph nodes; one `LEADER_OF` edge per (person, place, title) with
  all terms.
- **KB enrichment** (`--kb-min-mentions 3`, 0 = off): persons linked by full name in ≥ 3 news sentences who
  hold a Wikidata position at a graph node are added with that edge (`origin = "kb"`).

### Step 6 — Network statistics

```powershell
python src/stats.py    # -> reports/stats.json, stats.md, figures/*.png, data/graph/network.graphml (Gephi)
```

Density, degree distribution, components, clustering, assortativity, sampled shortest paths, PageRank and
betweenness (diplomatic edges), Louvain communities and modularity.

### Step 7 — Precision evaluation

```powershell
python src/evaluate.py sample --n 300       # stratified by relation type (floor 10) -> reports/eval_sample.csv
python src/evaluate.py sample-kb --n 60     # KB-enriched nodes                    -> reports/eval_kb_sample.csv
python src/evaluate.py merge-b              # copy annotator B's blind labels into eval_sample.csv
python src/evaluate.py score                # precision (raw + weighted), Wilson CI, kappa, per type / language
python src/evaluate.py score --file reports/eval_kb_sample.csv
```

Protocol used (rules in [reports/ANNOTATION_GUIDE.md](reports/ANNOTATION_GUIDE.md)):

- `label_a` (all 314 rows): LLM-assisted pre-labels (`eval_sample_claude.csv`), reviewed and corrected by one
  team member.
- `label_b` (blind subset of 108, `eval_subset_B.csv`): labelled independently by the other member.
- Disagreements are discussed; the agreed value is written to `label_b` in `eval_sample.csv`, or `ok` at the
  start of `note` marks a disputed triple as correct.
- Kappa uses the **original** blind labels in `eval_subset_B.csv`; precision uses the final labels.
  The weighted precision re-weights each relation type by its share in the graph (`eval_strata.json`).

> **Do not re-run `sample` / `sample-kb`**: they overwrite the labelled files.

---

## Daily commands

```powershell
conda activate politics-network
docker compose up -d        # start Neo4j
docker compose stop         # stop Neo4j (data is kept)
9router -n -t               # (extraction only) run 9router in the tray
```
