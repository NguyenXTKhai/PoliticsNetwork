// Example analysis queries for the diplomatic network (Neo4j Browser: run one at a time)

// Q1. Overview: node and relationship counts by type
MATCH (n:Entity) RETURN labels(n)[1] AS label, count(*) AS nodes ORDER BY nodes DESC;
MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS edges, sum(r.weight) AS mentions ORDER BY edges DESC;

// Q2. Vietnam's diplomatic partners: who met / called / visited / signed with Vietnam or its leaders most
MATCH (vn:Country {name: "Vietnam"})
OPTIONAL MATCH (p:Person)-[:LEADER_OF]->(vn)
WITH vn, collect(p) + [vn] AS side
UNWIND side AS v
MATCH (v)-[r:MET_WITH|PHONE_CALL|VISITED|SIGNED_AGREEMENT|UPGRADED_PARTNERSHIP]-(x)
WHERE NOT x IN side
RETURN x.name AS partner, labels(x)[1] AS type, sum(r.weight) AS mentions,
       collect(DISTINCT type(r)) AS relations
ORDER BY mentions DESC LIMIT 20;

// Q3. Who sanctions whom (countries / organizations), strongest first
MATCH (a)-[r:SANCTIONED]->(b)
RETURN a.name AS source, b.name AS target, r.weight AS sentences, r.first_date AS first, r.last_date AS last
ORDER BY sentences DESC LIMIT 20;

// Q4. Military aid suppliers of Ukraine
MATCH (a)-[r:MILITARY_AID]->(:Country {name: "Ukraine"})
RETURN a.name AS supplier, r.weight AS sentences, apoc.convert.fromJsonList(r.evidence)[0].sentence AS example
ORDER BY sentences DESC;

// Q5. Friend-or-foe balance: SUPPORTED vs CONDEMNED received by each country
MATCH (c:Country)
OPTIONAL MATCH (c)<-[s:SUPPORTED]-()
WITH c, coalesce(sum(s.weight), 0) AS support
OPTIONAL MATCH (c)<-[k:CONDEMNED]-()
WITH c, support, coalesce(sum(k.weight), 0) AS condemn
WHERE support + condemn >= 10
RETURN c.name AS country, support, condemn, round(1.0 * (support - condemn) / (support + condemn), 2) AS balance
ORDER BY condemn DESC LIMIT 20;

// Q6. Shortest diplomatic path between two leaders (ignoring structural edges)
MATCH (a:Person {name: "Tô Lâm"}), (b:Person {name: "Volodymyr Zelenskyy"}),
      p = shortestPath((a)-[:MET_WITH|PHONE_CALL|VISITED|SIGNED_AGREEMENT*..6]-(b))
RETURN [n IN nodes(p) | n.name] AS path, length(p) AS hops;

// Q7. Multilateral events with most participants
MATCH (x)-[:ATTENDED]->(e:Event)
RETURN e.name AS event, count(x) AS participants, collect(x.name)[..8] AS sample
ORDER BY participants DESC LIMIT 15;

// Q8. Evidence for one relationship (provenance; evidence is stored as a JSON string)
MATCH (a {name: "Donald Trump"})-[r:PHONE_CALL]-(b {name: "Vladimir Putin"})
RETURN r.weight, r.first_date, r.last_date, r.langs, [e IN apoc.convert.fromJsonList(r.evidence) | e.url] AS urls;

// Q9. GDS: PageRank on diplomatic edges (requires the GDS plugin)
CALL gds.graph.project('diplo', 'Entity',
  {MET_WITH: {orientation: 'UNDIRECTED', properties: 'weight'}, PHONE_CALL: {orientation: 'UNDIRECTED', properties: 'weight'},
   VISITED: {properties: 'weight'}, SIGNED_AGREEMENT: {orientation: 'UNDIRECTED', properties: 'weight'},
   SUPPORTED: {properties: 'weight'}, CONDEMNED: {properties: 'weight'}, SANCTIONED: {properties: 'weight'},
   MILITARY_AID: {properties: 'weight'}, ECONOMIC_AID: {properties: 'weight'}});
CALL gds.pageRank.stream('diplo', {relationshipWeightProperty: 'weight'})
YIELD nodeId, score
RETURN gds.util.asNode(nodeId).name AS entity, round(score, 3) AS pagerank ORDER BY pagerank DESC LIMIT 15;
CALL gds.graph.drop('diplo') YIELD graphName RETURN graphName;
