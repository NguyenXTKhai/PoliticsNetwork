# PoliticsNetwork

**A social network of Heads of State and International Organizations, extracted from diplomatic news with NLP.**

The project crawls the *World / International* sections of Vietnamese and English online newspapers (e.g. *Báo Thế giới & Việt Nam*, VnExpress, The Guardian), extracts diplomatic actions between presidents, prime ministers, governments and international organizations (UN, NATO, EU, ASEAN, ...), and stores the result as a property graph in **Neo4j**.

Target: ≥ 2,000 nodes, ≥ 5,000 edges, ≥ 10 relation types, ≥ 90% precision.

---

## Tech stack

| Layer | Tools |
|---|---|
| Crawling | `requests`, `trafilatura`, `beautifulsoup4`, `feedparser`, Guardian Open Platform API |
| NLP (Vietnamese) | `underthesea` (sentence split, NER) |
| NLP (English) | `spaCy` (`en_core_web_sm`) |
| Entity linking | Wikidata (`SPARQLWrapper`) alias dictionary + `rapidfuzz` |
| Relation extraction | Keyword rules + LLM via **[9router](https://9router.com/)** (OpenAI-compatible gateway, `openai` SDK) |
| Agent / orchestration | **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** (optional, pointed at the same 9router endpoint) |
| Graph database | **Neo4j 5.26** + APOC + Graph Data Science (Docker) |
| Analysis | `networkx`, `pandas`, `matplotlib`, Neo4j GDS |

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
python -m spacy download en_core_web_sm
```

If `conda` is not recognized in PowerShell, run once and reopen the terminal:

```powershell
<path-to-miniconda>\Scripts\conda.exe init powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned   # only if script execution is blocked
```

To add a package later: `pip install <pkg>` and append it to `requirements.txt`.

**VS Code:** `Ctrl+Shift+P` → *Python: Select Interpreter* → `politics-network`.

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

> `.env` is git-ignored. Never commit secrets.

### 5. Start 9router and create an extraction combo

```powershell
9router            # dashboard: http://localhost:20128
```

In the dashboard:

1. Connect at least one provider (Gemini, OpenRouter, ...).
2. Create a **combo** named `nlp-extract` with an ordered fallback list (e.g. a fast Gemini Flash model first, free OpenRouter models as fallback).
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

## Project structure

```
PoliticsNetwork/
├── cypher/
│   └── 00_schema.cypher     # constraints & indexes
├── data/
│   ├── raw/                 # crawled articles (git-ignored)
│   ├── processed/           # cleaned articles, sentences
│   ├── entities/            # Wikidata seeds & alias dictionary
│   └── graph/               # nodes.csv / edges.csv (mounted to Neo4j /import)
├── src/
│   └── check_env.py         # environment check (Neo4j + 9router)
├── docker-compose.yml       # Neo4j 5.26 + APOC + GDS
├── environment.yml          # conda environment
├── requirements.txt         # pip dependencies
└── .env.example             # environment variable template
```

### Pipeline (in progress)

| Step | Script | Status |
|---|---|---|
| Environment check | `src/check_env.py` | ✅ |
| Wikidata entity seeding & aliases | `src/seed_wikidata.py` | ⏳ |
| Crawling (Guardian, baoquocte.vn, VnExpress) | `src/crawl/` | ⏳ |
| Preprocessing (cleaning, sentence split, NER, entity linking) | `src/preprocess.py`, `src/ner_link.py` | ⏳ |
| Relation extraction (rules + LLM) | `src/extract_rel.py` | ⏳ |
| Graph building & Neo4j loading | `src/build_graph.py`, `src/load_neo4j.py` | ⏳ |
| Network statistics & evaluation | `src/stats.py`, `src/evaluate.py` | ⏳ |

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `Connection refused` from `cypher-shell` | Neo4j is still starting — wait for `Started.` in `docker logs neo4j-diplomacy` |
| Wrong Neo4j password | The password is only set on the **first** start. Stop the container, delete `./neo4j/data`, and run `docker compose up -d` again |
| Port 7474/7687 already in use | Another Neo4j container is running: `docker ps`, then stop it |
| `[FAIL] 9router` | Make sure `9router` is running, the API key is correct, and `LLM_MODEL` exists in `http://localhost:20128/v1/models` |
| Garbled Vietnamese in terminal | `chcp 65001` and `$env:PYTHONIOENCODING="utf-8"` |

---

## Team

| Member | Responsibilities |
|---|---|
| [@NguyenXTKhai](https://github.com/NguyenXTKhai) | _TBD_ |
| _Member 2_ | _TBD_ |
