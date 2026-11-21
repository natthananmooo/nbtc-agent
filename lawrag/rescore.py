"""
lawrag/rescore.py - ให้คะแนนใหม่จาก id ที่เก็บไว้ โดยไม่ต้องรันโมเดลอีกครั้ง

ทำไมให้คะแนนใหม่ได้โดยไม่ต้องค้นใหม่
    eval_retrieval.py เก็บ id ของทุกก้อนที่ค้นได้ ครบทั้ง 350 ครั้ง ลงไฟล์ไว้แล้ว
    การเปลี่ยนเฉลยคือการเปลี่ยนเกณฑ์ตัดสิน ไม่ได้เปลี่ยนสิ่งที่ค้นได้
    จึงนับใหม่จากไฟล์ได้เลย ใช้เวลาไม่ถึงวินาที แทนที่จะรันโมเดลอีกสามชั่วโมง

ทำไมแขนกราฟคำนวณใหม่ได้ด้วย
    รอบแรกมีบั๊ก rerank คืนมาครบ k ตัวพอดี ของที่เดินกราฟมาจึงไปอยู่อันดับ k+1
    แล้วถูกตัดทิ้งหมด ผลคือแขนกราฟเหมือนแขน rerank เป๊ะทั้ง 50 คำถาม ตรวจแล้ว 50/50
    เมื่อแขน rerank ที่เก็บไว้คือผลเดียวกับที่กราฟใช้เป็นจุดตั้งต้น
    และการเดินกราฟใช้แค่ law_graph.json ไม่ต้องใช้ embedding หรือ reranker
    จึงสร้างผลของแขนกราฟฉบับแก้บั๊กขึ้นมาใหม่จากไฟล์ได้ตรงกับที่โค้ดใหม่จะให้

    ยืนยันความถูกต้องได้ด้วยการรันของจริงสองสามข้อแล้วเทียบ
        docker compose --profile tools run --rm lawrag \\
            python -m lawrag.eval_retrieval --methods graph+rerank+ctx --limit 2 \\
            --out /data/law/verify.json

ไฟล์เข้า : /data/law/results_law_retrieval.json  ผลดิบจากการรัน
           /data/law/law_questions.json           เฉลยฉบับปัจจุบัน
           /data/law/law_graph.json               กราฟการอ้างอิง
ไฟล์ออก : /data/law/results_scored.json           คะแนนสุดท้าย พร้อมการวิเคราะห์ความไว
"""
from __future__ import annotations

import json
import os
import sys

from .lawgraph import traverse

BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
RAW = os.path.join(LAW_DIR, "results_law_retrieval.json")
QS = os.path.join(LAW_DIR, "law_questions.json")
GRAPH = os.path.join(LAW_DIR, "law_graph.json")
OUT = os.path.join(LAW_DIR, "results_scored.json")

GRAPH_HOPS = 2
GRAPH_CAP = 25
GRAPH_SLOTS = 5      # ต้องตรงกับค่าใน retrieve.py
GRAPH_METHOD = "graph+rerank+ctx"
GRAPH_BASE = "rerank+ctx_embedding+bm25"   # แขนที่กราฟใช้เป็นจุดตั้งต้น

# เฉลยทางเลือกของสองข้อที่ตัวค้นคืนข้ออื่นซึ่งตอบคำถามได้เหมือนกัน
# ใช้เป็นการวิเคราะห์ความไวเท่านั้น ไม่ใช่ผลหลัก
# เพราะการตัดสินว่าข้อเหล่านี้ใช้ได้ ต้องเห็นผลการทดลองก่อน จึงเป็นเกณฑ์ที่ปนเปื้อน
SENSITIVITY = {
    "L32": ["32_protect::ข้อ_7"],
    "L45": ["26_protect::ภาคผนวก_7", "26_protect::ภาคผนวก_8"],
}


# ส่วนที่ 1: เดินกราฟจากจุดตั้งต้น
# หน้าที่: คืน id ของ chunk ที่เดินไปถึง โดยไม่ซ้ำกับจุดตั้งต้น
def expand(nodes: dict, edges: list, seed_ids: list[str]) -> list[str]:
    seeds = ["chunk::" + i for i in seed_ids if "chunk::" + i in nodes]
    if not seeds:
        return []
    out = []
    for _, _, nid in traverse(nodes, edges, seeds, hops=GRAPH_HOPS, cap=GRAPH_CAP):
        if nodes.get(nid, {}).get("label") == "Chunk":
            cid = nid.replace("chunk::", "", 1)
            if cid not in seed_ids:
                out.append(cid)
    return out


# ส่วนที่ 2: สร้างผลของแขนกราฟฉบับแก้บั๊ก
# หน้าที่: กันช่องท้ายไว้ให้กราฟ แล้วคืนลำดับใหม่ · ตรงกับตรรกะใน retrieve.search
def graph_arm(nodes: dict, edges: list, base_ids: list[str], k: int) -> list[str]:
    extra = [e for e in expand(nodes, edges, base_ids[:5]) if e not in base_ids][:GRAPH_SLOTS]
    keep = max(1, k - GRAPH_SLOTS)
    return (base_ids[:keep] + extra + base_ids[keep:])[:k]


# ส่วนที่ 3: นับคะแนนของทุกวิธี
# รับ: ผลดิบ, แผนที่เฉลย · คืน: dict ชื่อวิธี -> รายการ id ของคำถามที่พลาด
def tally(records: list[dict], methods: list[str], gold_of: dict) -> dict:
    out = {}
    for m in methods:
        out[m] = [r["id"] for r in records
                  if not any(g in r["by_method"][m]["ids"] for g in gold_of[r["id"]])]
    return out


def main() -> int:
    for p in (RAW, QS, GRAPH):
        if not os.path.exists(p):
            print("ไม่พบ %s" % p, file=sys.stderr)
            return 1

    raw = json.load(open(RAW, encoding="utf-8"))
    qs = json.load(open(QS, encoding="utf-8"))["questions"]
    g = json.load(open(GRAPH, encoding="utf-8"))
    nodes = {n["id"]: n for n in g["nodes"]}
    edges = g["edges"]

    k = raw["k"]
    methods = raw["methods"]
    records = raw["records"]
    gold_of = {q["id"]: q["gold"] for q in qs}
    kind_of = {q["id"]: q.get("kind", "") for q in qs}

    # ตรวจว่าแขนกราฟของรอบนั้นเหมือนแขนตั้งต้นจริงไหม ถ้าไม่เหมือนแปลว่าบั๊กถูกแก้ไปแล้ว
    identical = sum(1 for r in records
                    if r["by_method"][GRAPH_METHOD]["ids"] == r["by_method"][GRAPH_BASE]["ids"])
    patched = identical == len(records)
    print("แขนกราฟในผลดิบ เหมือนแขน %s  %d/%d ข้อ"
          % (GRAPH_BASE, identical, len(records)))
    if patched:
        print("  แปลว่าผลดิบมาจากโค้ดก่อนแก้บั๊ก จะสร้างแขนกราฟฉบับแก้บั๊กให้ใหม่\n")
        for r in records:
            r["by_method"][GRAPH_METHOD]["ids"] = graph_arm(
                nodes, edges, r["by_method"][GRAPH_BASE]["ids"], k)
            r["by_method"][GRAPH_METHOD]["recomputed"] = True
    else:
        print("  แปลว่าผลดิบมาจากโค้ดที่แก้บั๊กแล้ว ใช้ตามนั้น\n")

    strict = tally(records, methods, gold_of)
    loose_gold = {i: gold_of[i] + SENSITIVITY.get(i, []) for i in gold_of}
    loose = tally(records, methods, loose_gold)

    n = len(records)
    print("%-28s %18s %18s" % ("วิธีค้น", "ไม่เจอ (เกณฑ์หลัก)", "เชิงอรรถ"))
    for m in methods:
        print("%-28s %8d %7.1f%% %8d %7.1f%%"
              % (m, len(strict[m]), 100 * len(strict[m]) / n,
                 len(loose[m]), 100 * len(loose[m]) / n))

    # แยกดูตามชนิดคำถาม เพื่อให้เห็นว่าวิธีไหนเก่งคำถามแบบไหน
    kinds = sorted({kind_of[r["id"]] for r in records})
    print("\nแยกตามชนิดคำถาม นับเฉพาะเกณฑ์หลัก")
    print("%-28s %s" % ("วิธีค้น", "  ".join("%-10s" % x for x in kinds)))
    by_kind = {}
    for m in methods:
        row, cells = {}, []
        for kd in kinds:
            tot = sum(1 for r in records if kind_of[r["id"]] == kd)
            bad = sum(1 for i in strict[m] if kind_of[i] == kd)
            row[kd] = {"failed": bad, "total": tot}
            cells.append("%-10s" % ("%d/%d" % (bad, tot)))
        by_kind[m] = row
        print("%-28s %s" % (m, "  ".join(cells)))

    out = {
        "k": k,
        "n_questions": n,
        "methods": methods,
        "graph_arm_recomputed": patched,
        "primary": {m: {"failed": len(strict[m]), "total": n,
                        "failure_rate": round(len(strict[m]) / n, 4),
                        "missed": strict[m]} for m in methods},
        "sensitivity": {m: {"failed": len(loose[m]), "total": n,
                            "failure_rate": round(len(loose[m]) / n, 4),
                            "missed": loose[m]} for m in methods},
        "sensitivity_note": "เฉลยทางเลือกนี้ตัดสินหลังเห็นผลการทดลอง จึงไม่ใช่ผลหลัก",
        "by_kind": by_kind,
        "mean_seconds_note": "เวลาในผลดิบเป็นต้นทุนส่วนเพิ่มเมื่อรันต่อจากวิธีก่อนหน้า "
                             "เพราะมี cache ของเวกเตอร์คำถามและผล rerank ไม่ใช่ต้นทุนถ้ารันเดี่ยว",
    }
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, OUT)
    print("\nเขียน %s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
