"""
ทดสอบชั้นเวกเตอร์กับกราฟ ด้วยค่าจริงจากฐานข้อมูล

รันในคอนเทนเนอร์ เพราะโมเดล embedding ถูกฝังไว้ใน image ของ mcp
    docker compose exec mcp python -m tests.test_rag
"""

import os
import sys

from tools import bm25, graph, rerank
from tools.resolve import resolve_filter_values


def head(t):
    print("\n" + "=" * 70 + "\n" + t + "\n" + "=" * 70)


head("1. ตัดคำไทยแล้วนับคำซ้ำ BM25")
if not bm25.available():
    print("ใช้ BM25 ไม่ได้:", bm25.load_error())
else:
    bm25.selftest()

head("2. cross-encoder ให้คะแนนความเกี่ยวข้องได้ชัดแค่ไหน")
if not rerank.available():
    print("โหลดโมเดลไม่สำเร็จ:", rerank.load_error())
    sys.exit(1)
rerank.selftest()

head("3. resolve ทั้งสามโหมด")
cases = [("เน็ตช้า", None), ("เน็ตช้า", "problem_detail"),
         ("ความเร็วไม่ได้ตามที่โฆษณา", None), ("เน็ตช้า", "problem_group"), ("โดนคิดเงินเกิน", "problem_group"),
         ("สัญญาณไม่ดี", "problem_group"), ("ข้อความโฆษณากวน", "problem_group"),
         ("เสาส่งสัญญาณอันตรายต่อสุขภาพ", "problem_group"), ("บริการ", "problem_group"),
         ("มือถือ", "service_type"), ("ดาวอังคาร", "province"),
         ("AIS", None), ("ทรู", None)]
for mode in ("off", "bm25", "rerank"):
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

head("5. ตรวจเกณฑ์ที่ตั้งใหม่กับเคสที่ต้องผ่านและต้องไม่ผ่าน")
os.environ["RAG_MODE"] = "rerank"
expect = [
    ("เน็ตช้า", "problem_group", "high", "คุณภาพสัญญาณ"),
    ("โดนคิดเงินเกิน", "problem_group", "high", "การคิดค่าบริการผิดพลาด"),
    ("เน็ตบ้าน", "service_type", "high", "อินเทอร์เน็ต"),
    ("ยกเลิกไม่ได้สักที", "problem_group", "ambiguous", None),
    ("บริการ", "problem_group", "ambiguous", None),
    ("ดาวอังคาร", "problem_group", "none", None),
    ("เน็ตช้า", None, "ambiguous", None),
]
ok = 0
for phrase, field, want_conf, want_val in expect:
    o = resolve_filter_values(phrase, field)
    got = o["confidence"]
    top = o["candidates"][0]["value"] if o["candidates"] else None
    good = got == want_conf and (want_val is None or top == want_val)
    ok += good
    print("  %-4s %-22s field=%-14s ได้ %-10s %s" % (
        "ผ่าน" if good else "ไม่ผ่าน", phrase, field, got, (top or "-")[:34]))
print("\nผ่าน %d จาก %d" % (ok, len(expect)))
