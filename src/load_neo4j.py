"""Step 5b - Load data/graph/{nodes,edges}.json into Neo4j.

Every node gets the label :Entity plus its type (:Person, :Country, :Organization, :Agreement, :Event).
Relationship type = edge type; properties: weight, confidence, dates, sources, evidence (as JSON text).

Usage:
    python src/load_neo4j.py            # wipe the database and reload
    python src/load_neo4j.py --keep     # MERGE into existing data
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

ROOT = Path(__file__).resolve().parents[1]
GRAPH = ROOT / "data" / "graph"
load_dotenv(ROOT / ".env")
BATCH = 1000


def chunks(lst, n=BATCH):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", action="store_true", help="do not delete existing data")
    args = ap.parse_args()

    nodes = json.loads((GRAPH / "nodes.json").read_text(encoding="utf-8"))
    edges = json.loads((GRAPH / "edges.json").read_text(encoding="utf-8"))
    drv = GraphDatabase.driver(os.getenv("NEO4J_URI", "bolt://localhost:7687"),
                               auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD")))
    with drv.session() as s:
        if not args.keep:
            s.run("MATCH (n) CALL (n) { DETACH DELETE n } IN TRANSACTIONS OF 5000 ROWS").consume()
        s.run("CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE").consume()

        by_label = defaultdict(list)
        for n in nodes:
            by_label[n["label"]].append({k: v for k, v in n.items() if k != "label" and v not in ("", None)})
        for label, rows in by_label.items():
            for part in chunks(rows):
                s.run(f"UNWIND $rows AS r MERGE (n:Entity {{id: r.id}}) SET n += r, n:{label}", rows=part).consume()
            print(f"  :{label:<13} {len(rows)}")

        by_type = defaultdict(list)
        for e in edges:
            props = {k: v for k, v in e.items() if k not in ("source", "target", "type")}
            for k in ("sources", "langs", "evidence"):
                props[k] = json.dumps(props.get(k) or {}, ensure_ascii=False)
            if e["evidence"]:
                props["example"] = e["evidence"][0]["sentence"]
                props["example_url"] = e["evidence"][0]["url"]
            by_type[e["type"]].append({"s": e["source"], "t": e["target"], "p": props})
        for typ, rows in sorted(by_type.items()):
            for part in chunks(rows):
                s.run(f"""UNWIND $rows AS r
                          MATCH (a:Entity {{id: r.s}}), (b:Entity {{id: r.t}})
                          CREATE (a)-[x:{typ}]->(b) SET x = r.p""", rows=part).consume()
            print(f"  [:{typ:<20}] {len(rows)}")

        n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
        m = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
        t = s.run("MATCH ()-[r]->() RETURN count(DISTINCT type(r)) AS c").single()["c"]
    drv.close()
    print(f"Neo4j: {n} nodes, {m} relationships, {t} relationship types")


if __name__ == "__main__":
    main()
