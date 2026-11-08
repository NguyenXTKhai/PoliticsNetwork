"""Kiem tra moi truong: Neo4j (APOC + GDS) va 9router (trich xuat quan he JSON).

Chay:  python src/check_env.py
"""
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()
ok = True

# ---------- 1. Neo4j ----------
try:
    from neo4j import GraphDatabase

    drv = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD")),
    )
    with drv.session() as s:
        apoc = s.run("RETURN apoc.version() AS v").single()["v"]
        gds = s.run("RETURN gds.version() AS v").single()["v"]
        n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    drv.close()
    print(f"[OK] Neo4j  | APOC {apoc} | GDS {gds} | {n} nodes")
except Exception as e:  # noqa: BLE001
    ok = False
    print(f"[FAIL] Neo4j: {e}")

# ---------- 2. 9router ----------
try:
    from openai import OpenAI

    client = OpenAI(base_url=os.getenv("LLM_BASE_URL"), api_key=os.getenv("LLM_API_KEY") or "x")
    sentence = (
        "Thủ tướng Phạm Minh Chính đã hội đàm với Tổng thống Hàn Quốc Lee Jae-myung, "
        "hai bên ký kết thỏa thuận hợp tác quốc phòng."
    )
    resp = client.chat.completions.create(
        model=os.getenv("LLM_MODEL"),
        temperature=0,
        messages=[
            {"role": "system", "content": "Bạn trích xuất quan hệ ngoại giao. Chỉ trả về JSON, không giải thích."},
            {
                "role": "user",
                "content": f'Câu: "{sentence}"\nTrả về JSON dạng '
                '{"relations":[{"source":str,"target":str,"relation":'
                '"MET_WITH|SIGNED_AGREEMENT|..."}]}',
            },
        ],
    )
    raw = resp.choices[0].message.content.strip().removeprefix("```json").removesuffix("```")
    print("[OK] 9router |", resp.model, "|", json.dumps(json.loads(raw), ensure_ascii=False))
except Exception as e:  # noqa: BLE001
    ok = False
    print(f"[FAIL] 9router: {e}")

sys.exit(0 if ok else 1)
