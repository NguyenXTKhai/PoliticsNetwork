"""Step 6 - Network statistics and figures for the report.

data/graph/{nodes,edges}.json  ->  reports/stats.json, reports/stats.md, reports/figures/*.png,
                                   data/graph/network.graphml (for Gephi)

Usage:
    python src/stats.py
"""
from __future__ import annotations

import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
GRAPH, REP = ROOT / "data" / "graph", ROOT / "reports"
FIG = REP / "figures"
DIPLO = ["MET_WITH", "PHONE_CALL", "VISITED", "SIGNED_AGREEMENT", "UPGRADED_PARTNERSHIP", "MILITARY_AID",
         "ECONOMIC_AID", "SANCTIONED", "EXPELLED_DIPLOMAT", "CONDEMNED", "SUPPORTED"]

# reference palette (light mode) - one hue per chart unless series identity is needed
INK, INK2, GRID, SURFACE = "#0b0b0b", "#52514e", "#e4e3df", "#fcfcfb"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "axes.edgecolor": GRID, "axes.labelcolor": INK2,
    "xtick.color": INK2, "ytick.color": INK2, "text.color": INK, "axes.grid": True, "grid.color": GRID,
    "grid.linewidth": 0.6, "axes.spines.top": False, "axes.spines.right": False, "font.size": 10,
    "axes.titlesize": 11, "axes.titleweight": "bold", "savefig.dpi": 200, "savefig.bbox": "tight",
})


def hbar(items: list[tuple[str, float]], title: str, xlabel: str, fname: str, fmt="{:,.0f}") -> None:
    labels, vals = zip(*items[::-1])
    fig, ax = plt.subplots(figsize=(7, 0.32 * len(items) + 1))
    ax.barh(labels, vals, color=SERIES[0], height=0.7)
    ax.grid(axis="y", visible=False)
    for y, v in enumerate(vals):
        ax.text(v, y, " " + fmt.format(v), va="center", fontsize=8, color=INK2)
    ax.set_xlim(0, max(vals) * 1.15)
    ax.set_title(title, loc="left")
    ax.set_xlabel(xlabel)
    fig.savefig(FIG / fname)
    plt.close(fig)


def main() -> None:
    FIG.mkdir(parents=True, exist_ok=True)
    nodes = json.loads((GRAPH / "nodes.json").read_text(encoding="utf-8"))
    edges = json.loads((GRAPH / "edges.json").read_text(encoding="utf-8"))
    name = {n["id"]: n["name"] for n in nodes}
    label = {n["id"]: n["label"] for n in nodes}

    G = nx.MultiDiGraph()
    for n in nodes:
        G.add_node(n["id"], label=n["label"], name=n["name"])
    for e in edges:
        G.add_edge(e["source"], e["target"], type=e["type"], weight=e["weight"])

    # simple undirected weighted projection used for structural metrics
    U = nx.Graph()
    U.add_nodes_from(G.nodes)
    for s, t, d in G.edges(data=True):
        if s != t:
            w = U[s][t]["weight"] + d["weight"] if U.has_edge(s, t) else d["weight"]
            U.add_edge(s, t, weight=w)
    comps = sorted(nx.connected_components(U), key=len, reverse=True)
    giant = U.subgraph(comps[0]).copy()

    random.seed(42)
    sample = random.sample(list(giant.nodes), min(300, giant.number_of_nodes()))
    dists = []
    for v in sample:
        dists += [d for d in nx.single_source_shortest_path_length(giant, v).values() if d > 0]

    deg = dict(U.degree())
    wdeg = dict(U.degree(weight="weight"))
    pr = nx.pagerank(nx.DiGraph([(s, t) for s, t, d in G.edges(data=True) if d["type"] in DIPLO]))
    btw = nx.betweenness_centrality(giant, k=min(500, giant.number_of_nodes()), seed=42)
    comms = nx.community.louvain_communities(giant, weight="weight", seed=42)
    comms = sorted(comms, key=len, reverse=True)
    modularity = nx.community.modularity(giant, comms, weight="weight")

    def top(d: dict, k=15) -> list[dict]:
        return [{"id": q, "name": name.get(q, q), "type": label.get(q, ""), "value": round(v, 5)}
                for q, v in sorted(d.items(), key=lambda x: -x[1])[:k]]

    etype = Counter(e["type"] for e in edges)
    stats = {
        "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
        "node_types": dict(Counter(label.values()).most_common()),
        "edge_types": dict(etype.most_common()),
        "relation_types": len(etype),
        "diplomatic_edges": sum(etype[t] for t in DIPLO),
        "diplomatic_mentions": sum(e["weight"] for e in edges if e["type"] in DIPLO),
        "density_directed": round(nx.density(nx.DiGraph(G)), 6),
        "avg_degree": round(2 * U.number_of_edges() / U.number_of_nodes(), 3),
        "connected_components": len(comps),
        "giant_component_nodes": giant.number_of_nodes(),
        "giant_component_share": round(giant.number_of_nodes() / U.number_of_nodes(), 4),
        "avg_clustering": round(nx.average_clustering(U), 4),
        "transitivity": round(nx.transitivity(U), 4),
        "degree_assortativity": round(nx.degree_assortativity_coefficient(U), 4),
        "avg_shortest_path_sampled": round(sum(dists) / len(dists), 3),
        "diameter_sampled_lower_bound": max(dists),
        "louvain_communities": len(comms), "modularity": round(modularity, 4),
        "top_degree": top(deg), "top_weighted_degree": top(wdeg), "top_pagerank": top(pr),
        "top_betweenness": top(btw),
        "top_communities": [{"size": len(c), "members": [name.get(q, q) for q in
                                                          sorted(c, key=lambda q: -wdeg[q])[:8]]}
                            for c in comms[:8]],
    }
    REP.mkdir(exist_ok=True)
    (REP / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------------- markdown summary (easy to paste into LaTeX tables)
    md = ["| Metric | Value |", "|---|---|"]
    md += [f"| {k} | {v} |" for k, v in stats.items() if not isinstance(v, (list, dict))]
    for key in ("node_types", "edge_types"):
        md += ["", f"| {key} | count |", "|---|---|"] + [f"| {k} | {v} |" for k, v in stats[key].items()]
    for key in ("top_degree", "top_pagerank", "top_betweenness"):
        md += ["", f"| {key} | type | value |", "|---|---|---|"]
        md += [f"| {r['name']} | {r['type']} | {r['value']} |" for r in stats[key]]
    md += ["", "| community | size | top members |", "|---|---|---|"]
    md += [f"| C{i + 1} | {c['size']} | {', '.join(c['members'])} |" for i, c in enumerate(stats["top_communities"])]
    (REP / "stats.md").write_text("\n".join(md), encoding="utf-8")

    # ---------------- figures
    hbar(etype.most_common(), "Edges by relation type", "edges", "edge_types.png")
    hbar(Counter(label.values()).most_common(), "Nodes by entity type", "nodes", "node_types.png")
    hbar([(r["name"], r["value"]) for r in stats["top_pagerank"]], "Top 15 entities by PageRank (diplomatic edges)",
         "PageRank", "top_pagerank.png", fmt="{:.4f}")
    hbar([(r["name"], r["value"]) for r in stats["top_degree"]], "Top 15 entities by degree", "distinct neighbours",
         "top_degree.png")

    hist = Counter(deg.values())
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.scatter(list(hist), list(hist.values()), s=14, color=SERIES[0], edgecolor=SURFACE, linewidth=0.8)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_title("Degree distribution (log-log)", loc="left")
    ax.set_xlabel("degree k")
    ax.set_ylabel("number of nodes with degree k")
    fig.savefig(FIG / "degree_distribution.png")
    plt.close(fig)

    # monthly evidence by language (2 series -> legend + direct labels)
    monthly = defaultdict(Counter)
    rel_path = ROOT / "data" / "processed" / "relations.jsonl"
    latest = {json.loads(line)["sid"]: line for line in rel_path.open(encoding="utf-8")}
    for line in latest.values():
        r = json.loads(line)
        if r["relations"] and r.get("date", "")[:7] >= "2016-01":
            monthly[r["lang"]][r["date"][:7]] += len(r["relations"])
    months = sorted({m for c in monthly.values() for m in c})
    fig, ax = plt.subplots(figsize=(8, 3.6))
    for i, lang in enumerate(sorted(monthly)):
        ys = [monthly[lang][m] for m in months]
        ax.plot(range(len(months)), ys, color=SERIES[i], linewidth=2, label={"en": "English", "vi": "Vietnamese"}[lang])
        ax.text(len(months) - 1, ys[-1], f" {lang.upper()}", color=INK2, va="center", fontsize=8)
    step = max(1, len(months) // 10)
    ax.set_xticks(range(0, len(months), step), months[::step], rotation=45, ha="right")
    ax.set_title("Extracted relations per month by language", loc="left")
    ax.set_ylabel("relations")
    if len(monthly) > 1:
        ax.legend(frameon=False, loc="lower left", bbox_to_anchor=(0, 1.02), ncol=len(monthly))
        ax.set_title("Extracted relations per month by language", loc="left", pad=24)
    fig.savefig(FIG / "relations_per_month.png")
    plt.close(fig)

    # ---------------- Gephi export (scalar attributes only)
    H = nx.MultiDiGraph()
    for n in nodes:
        H.add_node(n["id"], label=n["name"], type=n["label"], degree=n.get("degree", 0))
    for e in edges:
        H.add_edge(e["source"], e["target"], type=e["type"], weight=float(e["weight"]))
    nx.write_graphml(H, GRAPH / "network.graphml")

    print(json.dumps({k: v for k, v in stats.items() if not isinstance(v, list)}, ensure_ascii=False, indent=1))
    print(f"-> reports/stats.json, reports/stats.md, reports/figures/*.png, data/graph/network.graphml")


if __name__ == "__main__":
    main()
