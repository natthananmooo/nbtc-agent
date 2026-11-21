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

เซฟทุกข้อ ไม่ใช่ตอนจบ
    เครื่องที่ใช้วัดมีแรมจำกัด ถ้าโดนฆ่ากลางทางแล้วเซฟตอนจบอย่างเดียว
    ผลที่ทำมาหลายชั่วโมงจะหายหมด จึงเขียนไฟล์ใหม่ทุกครั้งที่จบหนึ่งคำถาม
    และตอนเริ่มจะอ่านไฟล์เดิมก่อน ข้อไหนทำครบทุกวิธีแล้วจะข้ามไป
    รันคำสั่งเดิมซ้ำจึงทำงานต่อจากจุดที่ค้าง ไม่ใช่เริ่มใหม่

ตัวอย่างการใช้
    python -m lawrag.eval_retrieval --limit 1          จับเวลาคำถามเดียวก่อน
    python -m lawrag.eval_retrieval                    วัดทั้งชุด
    python -m lawrag.eval_retrieval --methods ctx_embedding,graph+rerank+ctx
    python -m lawrag.eval_retrieval --fresh            ไม่สนของเดิม เริ่มนับหนึ่งใหม่
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


# ส่วนที่ 1.1: เขียนผลลงไฟล์
# หน้าที่: สรุปรายวิธีแล้วเขียนไฟล์ เรียกได้ทั้งกลางทางและตอนจบ
# รับ: พาธไฟล์, รายการผล, ชื่อวิธี, ค่า k, วินาทีที่ใช้ · คืน: dict สรุปรายวิธี
def save(path: str, records: list[dict], methods: list[str], k: int,
         elapsed: float) -> dict:
    summary = {}
    for m in methods:
        got = [r for r in records if m in r["by_method"]]
        if not got:
            continue
        miss = sum(1 for r in got if not r["by_method"][m]["hit"])
        secs = [r["by_method"][m]["seconds"] for r in got]
        ranks = [r["by_method"][m]["rank"] for r in got if r["by_method"][m]["rank"]]
        summary[m] = {
            "failed": miss,
            "total": len(got),
            "failure_rate": round(miss / len(got), 4),
            "mean_seconds": round(sum(secs) / len(secs), 2),
            "mean_rank_when_found": round(sum(ranks) / len(ranks), 2) if ranks else None,
        }
    out = {"k": k, "n_questions": len(records), "methods": methods,
           "summary": summary, "records": records,
           "total_seconds": round(elapsed, 1)}
    # เขียนไฟล์ชั่วคราวก่อนแล้วค่อยสลับชื่อ ถ้าถูกฆ่ากลางการเขียน ไฟล์เดิมจะไม่พัง
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)
    return summary


# ส่วนที่ 2: จุดเริ่มทำงาน
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="ทำแค่ n คำถามแรก 0 คือทำทั้งหมด")
    ap.add_argument("--methods", default="",
                    help="ชื่อวิธีคั่นด้วยจุลภาค เว้นว่างคือทำทุกวิธี")
    ap.add_argument("--k", type=int, default=20,
                    help="นับว่าเจอถ้าเฉลยอยู่ใน k อันดับแรก")
    ap.add_argument("--questions", default=Q_PATH,
                    help="ไฟล์คำถาม ใช้สลับชุดได้ เช่นชุดคำถามข้ามข้อของการทดลองที่สอง")
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--fresh", action="store_true",
                    help="ไม่อ่านผลเดิม เริ่มนับหนึ่งใหม่")
    a = ap.parse_args()

    if not os.path.exists(a.questions):
        print("ไม่พบ %s ให้สร้างจากไฟล์ yaml ก่อน" % a.questions, file=sys.stderr)
        return 1
    questions = json.load(open(a.questions, encoding="utf-8"))["questions"]
    if a.limit:
        questions = questions[:a.limit]

    methods = [m.strip() for m in a.methods.split(",") if m.strip()] or list(METHODS)
    bad = [m for m in methods if m not in METHODS]
    if bad:
        print("ไม่รู้จักวิธี %s · ที่มีคือ %s" % (bad, list(METHODS)), file=sys.stderr)
        return 1

    # อ่านผลเดิมถ้ามี เพื่อทำต่อจากจุดที่ค้าง
    done: dict[str, dict] = {}
    if os.path.exists(a.out) and not a.fresh:
        try:
            prev = json.load(open(a.out, encoding="utf-8"))
            if prev.get("k") == a.k:
                done = {r["id"]: r for r in prev.get("records", [])}
        except Exception as e:
            print("อ่านผลเดิมไม่ได้ จะเริ่มใหม่ · %s" % e, file=sys.stderr)
    todo = sum(1 for q in questions
               for m in methods if m not in done.get(q["id"], {}).get("by_method", {}))
    if done:
        print("เจอผลเดิม %d ข้อ · เหลือต้องค้นอีก %d ครั้ง" % (len(done), todo), flush=True)

    print("คำถาม %d ข้อ × วิธี %d แบบ = %d ครั้ง · นับที่ top-%d\n"
          % (len(questions), len(methods), len(questions) * len(methods), a.k),
          flush=True)
    if todo == 0:
        print("ทำครบแล้วทุกข้อ ไม่มีอะไรต้องรัน ใส่ --fresh ถ้าอยากรันใหม่")
        return 0

    # สร้างตัวค้นครั้งเดียว โมเดลจึงโหลดรอบเดียวสำหรับทั้งชุด
    t0 = time.time()
    r = LawRetriever(with_rerank=True, with_graph=True)
    print("โหลดโมเดลและดัชนีเสร็จใน %.0f วินาที · มี %d chunk\n"
          % (time.time() - t0, len(r.meta)), flush=True)

    records = []
    t_all = time.time()
    fresh_q = 0           # นับเฉพาะข้อที่ค้นจริงในรอบนี้ ใช้ประมาณเวลาที่เหลือ
    for n, item in enumerate(questions, 1):
        gold = item["gold"]
        per = dict(done.get(item["id"], {}).get("by_method", {}))
        missing = [m for m in methods if m not in per]
        for m in missing:
            per[m] = run_one(r, item["q"], gold, m, a.k)
        records.append({"id": item["id"], "q": item["q"], "gold": gold,
                        "kind": item.get("kind", ""), "by_method": per})

        used = time.time() - t_all
        mark = "".join("o" if per[m]["hit"] else "x" for m in methods)
        if not missing:
            print("[%2d/%2d] %-5s %s  %s  (ใช้ผลเดิม)"
                  % (n, len(questions), item["id"], mark, item["q"][:42]), flush=True)
            continue

        fresh_q += 1
        # เซฟทุกข้อ ถ้าโดนฆ่ากลางทางจะไม่เสียของที่ทำมา
        save(a.out, records, methods, a.k, used)
        left = sum(1 for q in questions[n:]
                   if any(m not in done.get(q["id"], {}).get("by_method", {})
                          for m in methods))
        eta = (used / fresh_q) * left / 60
        print("[%2d/%2d] %-5s %s  %s  %.0f วินาทีสะสม · เหลืออีก %d ข้อ ~%.0f นาที"
              % (n, len(questions), item["id"], mark, item["q"][:42],
                 used, left, eta), flush=True)

    # สรุปรายวิธี
    print("\n%-28s %8s %10s %13s"
          % ("วิธีค้น", "ไม่เจอ", "อัตราพลาด", "วินาทีเฉลี่ย"))
    summary = save(a.out, records, methods, a.k, time.time() - t_all)
    for m in methods:
        g = summary.get(m)
        if g:
            print("%-28s %8d %9.1f%% %12.1f"
                  % (m, g["failed"], 100 * g["failure_rate"], g["mean_seconds"]))

    print("\nใช้เวลารอบนี้ %.0f วินาที · เขียน %s"
          % (time.time() - t_all, a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
