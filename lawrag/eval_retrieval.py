"""
lawrag/eval_retrieval.py - วัดว่าแต่ละวิธีค้น หาข้อกฎหมายที่ถูกเจอไหม

ตัววัดคือ Failed Retrievals @k ตัวเดียวกับที่ Anthropic ใช้ในบทความ Contextual Retrieval
    นับว่าในผล k อันดับแรก มีข้อที่เป็นเฉลยอยู่ไหม
    ถ้าไม่มีแม้แต่ข้อเดียว นับเป็น failed หนึ่งครั้ง
    อัตราพลาด = failed / จำนวนคำถาม   ยิ่งน้อยยิ่งดี

ทำไมขั้นนี้ไม่เรียก LLM
    ถ้าให้ LLM เป็นคนเรียกค้น แล้ววิธีหนึ่งได้คะแนนแย่กว่าอีกวิธี
    เราจะแยกไม่ออกว่าตัวค้นแย่ หรือ LLM เลือกคำค้นไม่ดี
    ตัดออกไปเลยเหลือตัวแปรเดียวคือวิธีค้น ซึ่งเป็นวิธีเดียวกับที่ Anthropic วัด

ไฟล์เข้า : /data/law/law_questions.json  สร้างจาก eval/law_questions.yaml
ไฟล์ออก : /data/law/results_law_retrieval.json  เก็บ id ที่ค้นได้ครบทุกคำถามทุกวิธี
           ไฟล์นี้เอาไปใช้ซ้ำในการวัดชั้นตอบได้ ไม่ต้องค้นใหม่

ตัวอย่างการใช้
    python -m lawrag.eval_retrieval --limit 1          จับเวลาคำถามเดียวก่อน
    python -m lawrag.eval_retrieval                    วัดทั้งชุด
    python -m lawrag.eval_retrieval --methods ctx_embedding,graph+rerank+ctx
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

from .retrieve import METHODS, LawRetriever

BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
Q_PATH = os.path.join(LAW_DIR, "law_questions.json")
OUT_PATH = os.path.join(LAW_DIR, "results_law_retrieval.json")


# ส่วนที่ 1: วัดคำถามเดียวกับวิธีเดียว
# หน้าที่: ค้นแล้วบอกว่าเฉลยติดอันดับไหม อันดับที่เท่าไหร่ และใช้เวลาเท่าไหร่
# รับ: ตัวค้น, คำถาม, เฉลย, ชื่อวิธี, จำนวนอันดับที่นับ
# คืน: dict ผลของคำถามนี้กับวิธีนี้
def run_one(r: LawRetriever, q: str, gold: list[str], method: str, k: int) -> dict:
    t0 = time.time()
    ids = r.search(q, method, k=k)
    sec = time.time() - t0
    # อันดับที่เจอเฉลยตัวแรก นับจาก 1 ถ้าไม่เจอเลยเป็น None
    rank = next((i + 1 for i, cid in enumerate(ids) if cid in gold), None)
    return {
        "method": method,
        "ids": ids,
        "hit": rank is not None,
        "rank": rank,
        "seconds": round(sec, 2),
    }


# ส่วนที่ 2: จุดเริ่มทำงาน
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="ทำแค่ n คำถามแรก 0 คือทำทั้งหมด")
    ap.add_argument("--methods", default="",
                    help="ชื่อวิธีคั่นด้วยจุลภาค เว้นว่างคือทำทุกวิธี")
    ap.add_argument("--k", type=int, default=20,
                    help="นับว่าเจอถ้าเฉลยอยู่ใน k อันดับแรก")
    ap.add_argument("--out", default=OUT_PATH)
    a = ap.parse_args()

    if not os.path.exists(Q_PATH):
        print("ไม่พบ %s ให้สร้างจาก eval/law_questions.yaml ก่อน" % Q_PATH,
              file=sys.stderr)
        return 1
    questions = json.load(open(Q_PATH, encoding="utf-8"))["questions"]
    if a.limit:
        questions = questions[:a.limit]

    methods = [m.strip() for m in a.methods.split(",") if m.strip()] or list(METHODS)
    bad = [m for m in methods if m not in METHODS]
    if bad:
        print("ไม่รู้จักวิธี %s · ที่มีคือ %s" % (bad, list(METHODS)), file=sys.stderr)
        return 1

    print("คำถาม %d ข้อ × วิธี %d แบบ = %d ครั้ง · นับที่ top-%d\n"
          % (len(questions), len(methods), len(questions) * len(methods), a.k),
          flush=True)

    # สร้างตัวค้นครั้งเดียว โมเดลจึงโหลดรอบเดียวสำหรับทั้งชุด
    t0 = time.time()
    r = LawRetriever(with_rerank=True, with_graph=True)
    print("โหลดโมเดลและดัชนีเสร็จใน %.0f วินาที · มี %d chunk\n"
          % (time.time() - t0, len(r.meta)), flush=True)

    records = []
    t_all = time.time()
    for n, item in enumerate(questions, 1):
        gold = item["gold"]
        per = {}
        for m in methods:
            res = run_one(r, item["q"], gold, m, a.k)
            per[m] = res
        records.append({"id": item["id"], "q": item["q"], "gold": gold,
                        "kind": item.get("kind", ""), "by_method": per})
        # พิมพ์ทีละข้อ จะได้เห็นความคืบหน้าและประมาณเวลาที่เหลือได้
        used = time.time() - t_all
        mark = "".join("o" if per[m]["hit"] else "x" for m in methods)
        print("[%2d/%2d] %-5s %s  %s  %.0f วินาทีสะสม"
              % (n, len(questions), item["id"], mark, item["q"][:42], used),
              flush=True)

    # สรุปรายวิธี
    print("\n%-28s %8s %10s %13s"
          % ("วิธีค้น", "ไม่เจอ", "อัตราพลาด", "วินาทีเฉลี่ย"))
    summary = {}
    for m in methods:
        miss = sum(1 for rec in records if not rec["by_method"][m]["hit"])
        secs = [rec["by_method"][m]["seconds"] for rec in records]
        ranks = [rec["by_method"][m]["rank"] for rec in records
                 if rec["by_method"][m]["rank"]]
        summary[m] = {
            "failed": miss,
            "total": len(records),
            "failure_rate": round(miss / len(records), 4) if records else None,
            "mean_seconds": round(sum(secs) / len(secs), 2) if secs else None,
            "mean_rank_when_found": round(sum(ranks) / len(ranks), 2) if ranks else None,
        }
        print("%-28s %8d %9.1f%% %12.1f"
              % (m, miss, 100 * miss / max(len(records), 1),
                 sum(secs) / max(len(secs), 1)))

    out = {
        "k": a.k,
        "n_questions": len(records),
        "methods": methods,
        "summary": summary,
        "records": records,
        "total_seconds": round(time.time() - t_all, 1),
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("\nใช้เวลาทั้งหมด %.0f วินาที · เขียน %s"
          % (out["total_seconds"], a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
