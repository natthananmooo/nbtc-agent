"""
eval/run_eval.py - รันชุดคำถามทดสอบ คิดคะแนนสองแกน แล้วส่งเข้า Langfuse

    python3 -m eval.run_eval --selftest            ตรวจว่าเครื่องวัดเองถูก ไม่ต้องมีคีย์
    python3 -m eval.run_eval --label "ไม่มี RAG"    รันจริงผ่าน OpenRouter
    python3 -m eval.run_eval --label "มี RAG"

ผลออกเป็น eval/results_<label>.json เอาไปวาดแผนภาพสี่ช่องต่อได้
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from eval.score import correctness, faithfulness
from eval.truth import ground_truth, load_cases

OUT_DIR = Path(__file__).resolve().parent


def _perfect_answer(case: dict, truth: dict) -> str:
    """คำตอบในอุดมคติ สร้างจากเฉลย ใช้ตรวจว่าเครื่องวัดทำงานถูก"""
    if truth["kind"] == "clarify":
        return "คำถามนี้กำกวม หมายถึงแบบไหนครับ"
    parts = []
    for r in truth["rows"][: truth.get("top_n", 1)]:
        parts.append(" ".join(
            ("{:,}".format(v) if isinstance(v, int) else str(v))
            for k, v in r.items() if k != "n"))
    for r in truth.get("rows_b", [])[: truth.get("top_n", 1)]:
        parts.append(" ".join(str(v) for k, v in r.items() if k != "n"))
    return " | ".join(parts) + "\nที่มา: " + truth.get("sql", "")


def _selftest_context(truth: dict) -> list[dict]:
    if truth["kind"] == "clarify":
        return []
    body = {"rows": truth["rows"], "sql_used": truth.get("sql", ""),
            "warnings": truth.get("warnings", [])}
    if "rows_b" in truth:
        body["rows_b"] = truth["rows_b"]
    return [{"tool": "query_complaints", "kind": "retriever",
             "output": json.dumps(body, ensure_ascii=False)}]


async def run_real(cases, label):
    """รันผ่าน agent จริง ต้องมี OPENROUTER_API_KEY และ MCP Server"""
    import agent
    from observability import tracer
    from openai import OpenAI

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("ยังไม่ได้ตั้ง OPENROUTER_API_KEY ใน .env")

    client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
    results = []

    async with agent.open_session() as session:
        system, oa_tools = await agent.build_context(session)
        for case in cases:
            truth = ground_truth(case)
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": case["question"]}]
            # คำถามเดียวพังต้องไม่ทำให้ทั้งรอบหาย ไม่งั้นต้องรันใหม่หมด
            # ข้อที่พังนับเป็นศูนย์ทั้งสองแกน ซึ่งตรงความจริง คือระบบตอบไม่ได้
            try:
                answer = await agent.run_turn(session, client, messages, oa_tools,
                                              case["question"])
                ctx = list(tracer.context)
                one = _score_one(case, truth, answer, ctx, tracer, system)
            except Exception as e:
                one = _score_one(case, truth, "", list(tracer.context), tracer, system)
                one["error"] = "%s: %s" % (type(e).__name__, e)
                print("  %-4s ล้มเหลว %s" % (case["id"], one["error"][:90]))
                results.append(one)
                continue
            results.append(one)
            print("  %-4s C=%.2f F=%.2f" % (case["id"],
                  one["correctness"], one["faithfulness"]))
    return results


def _score_one(case, truth, answer, ctx, tracer=None, schema=""):
    c, c_detail = correctness(case, answer, truth)
    f, unsupported = faithfulness(answer, ctx, schema)
    if tracer is not None and tracer.enabled:
        tracer.add_score("correctness", c, json.dumps(c_detail, ensure_ascii=False))
        tracer.add_score("faithfulness", f,
                         "ตัวเลขที่หาที่มาไม่ได้: %s" % unsupported if unsupported else "")
    return {
        "id": case["id"], "type": case["type"], "question": case["question"],
        "answer": answer, "correctness": c, "faithfulness": f,
        "unsupported_numbers": unsupported, "detail": c_detail,
    }


def _bad_answer(case: dict, truth: dict) -> str:
    """คำตอบที่จงใจผิด ใช้ตรวจว่าเครื่องวัดจับผิดได้จริง ไม่ใช่ให้ 1.0 ทุกครั้ง"""
    if truth["kind"] == "clarify":
        return "มี 1,234 เรื่องครับ"          # เดาแทนที่จะถามกลับ
    parts = []
    for r in truth["rows"][: truth.get("top_n", 1)]:
        parts.append(" ".join(
            str(v + 7777) if isinstance(v, (int, float)) else "หน่วยงานสมมติ"
            for k, v in r.items() if k != "n"))
    return " | ".join(parts)


def run_selftest(cases, bad=False):
    results = []
    for case in cases:
        truth = ground_truth(case)
        answer = (_bad_answer if bad else _perfect_answer)(case, truth)
        results.append(_score_one(case, truth, answer, _selftest_context(truth)))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default=os.environ.get("RUN_LABEL", "ไม่มี RAG"))
    ap.add_argument("--selftest", action="store_true",
                    help="ตรวจเครื่องวัดด้วยคำตอบในอุดมคติ ไม่เรียก LLM")
    ap.add_argument("--selftest-bad", action="store_true",
                    help="ตรวจว่าเครื่องวัดจับคำตอบผิดได้ ไม่ใช่ให้เต็มทุกครั้ง")
    args = ap.parse_args()

    os.environ["RUN_LABEL"] = args.label
    cases = load_cases()
    print("รอบ: %s | %d คำถาม%s\n" % (args.label, len(cases),
          " (โหมดตรวจเครื่องวัด)" if args.selftest
          else " (โหมดตรวจด้วยคำตอบผิด)" if args.selftest_bad else ""))

    if args.selftest or args.selftest_bad:
        results = run_selftest(cases, bad=args.selftest_bad)
    else:
        results = asyncio.run(run_real(cases, args.label))

    by_type: dict[str, list] = {}
    for r in results:
        by_type.setdefault(r["type"], []).append(r)

    print("\n%-10s %5s  %12s  %13s" % ("ชนิด", "ข้อ", "correctness", "faithfulness"))
    for t, rs in by_type.items():
        print("%-10s %5d  %12.3f  %13.3f" % (
            t, len(rs),
            sum(x["correctness"] for x in rs) / len(rs),
            sum(x["faithfulness"] for x in rs) / len(rs)))
    print("%-10s %5d  %12.3f  %13.3f" % (
        "รวม", len(results),
        sum(x["correctness"] for x in results) / len(results),
        sum(x["faithfulness"] for x in results) / len(results)))

    if args.selftest or args.selftest_bad:
        # โหมดตรวจเครื่องวัด ไม่บันทึกไฟล์ผล กันสับสนกับผลรันจริง
        print("\n(โหมดตรวจเครื่องวัด ไม่บันทึกไฟล์ผล)")
        return

    safe = args.label.replace(" ", "_").replace("/", "_")
    path = OUT_DIR / ("results_%s.json" % safe)
    path.write_text(json.dumps({"label": args.label, "results": results},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nบันทึกที่", path)

    low = [r for r in results if r["correctness"] < 1.0 or r["faithfulness"] < 1.0]
    if low:
        print("\nข้อที่ยังไม่เต็ม")
        for r in low:
            print("  %-4s C=%.2f F=%.2f  %s"
                  % (r["id"], r["correctness"], r["faithfulness"], r["question"][:46]))


if __name__ == "__main__":
    main()
