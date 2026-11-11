// Constraints & indexes for the diplomatic graph.
// load_neo4j.py already creates the entity_id constraint; the name indexes below are optional (faster lookups).
CREATE CONSTRAINT entity_id IF NOT EXISTS FOR (n:Entity) REQUIRE n.id IS UNIQUE;
CREATE INDEX person_name IF NOT EXISTS FOR (p:Person) ON (p.name);
CREATE INDEX country_name IF NOT EXISTS FOR (c:Country) ON (c.name);
CREATE INDEX org_name IF NOT EXISTS FOR (o:Organization) ON (o.name);
CREATE FULLTEXT INDEX entity_names IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.name_vi];
