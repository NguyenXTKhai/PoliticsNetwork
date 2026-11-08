// Rang buoc & chi muc cho do thi ngoai giao
CREATE CONSTRAINT entity_qid IF NOT EXISTS FOR (n:Entity) REQUIRE n.qid IS UNIQUE;
CREATE CONSTRAINT article_url IF NOT EXISTS FOR (a:Article) REQUIRE a.url IS UNIQUE;
CREATE INDEX person_name IF NOT EXISTS FOR (p:Person) ON (p.name);
CREATE INDEX country_name IF NOT EXISTS FOR (c:Country) ON (c.name);
CREATE INDEX org_name IF NOT EXISTS FOR (o:Organization) ON (o.name);
CREATE FULLTEXT INDEX entity_names IF NOT EXISTS FOR (n:Entity) ON EACH [n.name, n.name_vi];
