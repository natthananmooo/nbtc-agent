"""
ทดสอบชั้นเวกเตอร์กับกราฟ ด้วยค่าจริงจากฐานข้อมูล

รันในคอนเทนเนอร์ เพราะโมเดล embedding ถูกฝังไว้ใน image ของ mcp
    docker compose exec mcp python -m tests.test_rag
"""

import os
import sys

from tools import bm25, embed, graph
from tools.resolve import resolve_filter_values


def head(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70)


head("1. ตัดคำไทยแล้วนับคำซ้ำ BM25")
if not bm25.available():
    print("ใช้ BM25 ไม่ได้:", bm25.load_error())
else:
    bm25.selftest()

head("2. โมเดล embedding จับความหมายภาษาไทยได้จริงไหม")
if not embed.available():
    print("โหลดโมเดลไม่สำเร็จ:", embed.load_error())
    sys.exit(1)
embed.selftest()

head("3. resolve ทั้งสามโหมด")
cases = [("เน็ตช้า", None), ("เน็ตช้า", "problem_detail"),
         ("ความเร็วไม่ได้ตามที่โฆษณา", None), ("เน็ตช้า", "problem_group"), ("โดนคิดเงินเกิน", "problem_group"),
         ("สัญญาณไม่ดี", "problem_group"), ("ข้อความโฆษณากวน", "problem_group"),
         ("เสาส่งสัญญาณอันตรายต่อสุขภาพ", "problem_group"), ("บริการ", "problem_group"),
         ("มือถือ", "service_type"), ("ดาวอังคาร", "province"),
         ("AIS", None), ("ทรู", None)]
for mode in ("off", "bm25", "vector"):
    os.environ["RAG_MODE"] = mode
    print("\nRAG_MODE=%s" % mode)
    for phrase, field in cases:
        o = resolve_filter_values(phrase, field)
        top = o["candidates"][0]["value"][:40] if o["candidates"] else "-"
        print("  %-18s %-10s %-8s %s" % (phrase, o["confidence"], o.get("method"), top))

head("4. กราฟความสัมพันธ์")
g = graph.graph()
print("node %d  edge %d" % (g.number_of_nodes(), g.number_of_edges()))
o = graph.explore_relations("บริษัท ทรู มูฟ เอช ยูนิเวอร์แซล คอมมิวนิเคชั่น จำกัด",
                            "provider", to_field="problem_group", top_k=5)
for d in o["related"]["problem_group"]:
    print("  %-44s rows=%-6d share=%5.1f%%  lift=%.2f" % (
        d["value"][:44], d["rows_all_years"], d["share_pct"], d["lift"]))
