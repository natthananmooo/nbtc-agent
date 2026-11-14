"""
eval/truth.py - คำนวณเฉลยของแต่ละคำถามจากฐานข้อมูลโดยตรง

ไม่ใช้ LLM และไม่เขียนตัวเลขด้วยมือ
เฉลยมาจากการเรียก query_complaints ด้วยพารามิเตอร์ที่คนกำหนดไว้ใน questions.yaml
ถ้าข้อมูลต้นทางเปลี่ยน เฉลยเปลี่ยนตามเอง
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tools.query import query_complaints
from tools.resolve import resolve_filter_values

QUESTIONS = Path(__file__).resolve().parent / "questions.yaml"


def load_cases() -> list[dict]:
    return yaml.safe_load(QUESTIONS.read_text(encoding="utf-8"))["cases"]


def _resolved_values(spec: dict) -> list[str]:
    r = resolve_filter_values(spec["phrase"], spec.get("field"))
    return [c["value"] for c in r["candidates"]]


def ground_truth(case: dict) -> dict:
    """คืนเฉลยของหนึ่งคำถาม ถ้าเป็นชนิด clarify จะไม่มีตัวเลข"""
    if case["type"] == "clarify":
        return {"kind": "clarify", "why": case.get("why", "")}

    q = dict(case.get("query") or {})
    filters = dict(q.get("filters") or {})

    if "resolve" in case:
        spec = case["resolve"]
        filters[spec["field"]] = _resolved_values(spec)
    filters.update(case.get("extra_filters") or {})
    if filters:
        q["filters"] = filters
    q = {k: v for k, v in q.items() if v is not None}

    res = query_complaints(**q)
    out = {
        "kind": "rows",
        "rows": res["rows"],
        "sql": res["sql_used"],
        "warnings": res["warnings"],
        "top_n": case.get("top_n", 1),
    }

    if "compare_with" in case:
        f2 = dict(case.get("query", {}).get("filters") or {})
        spec = case["compare_with"]
        f2[spec["field"]] = _resolved_values(spec)
        q2 = dict(case["query"]); q2["filters"] = f2
        q2 = {k: v for k, v in q2.items() if v is not None}
        res2 = query_complaints(**q2)
        out["rows_b"] = res2["rows"]
        out["sql_b"] = res2["sql_used"]
    return out


if __name__ == "__main__":
    import json
    for c in load_cases():
        t = ground_truth(c)
        if t["kind"] == "clarify":
            print("%-4s clarify  %s" % (c["id"], c["question"][:48]))
        else:
            head = json.dumps(t["rows"][: t["top_n"]], ensure_ascii=False)
            extra = ""
            if "rows_b" in t:
                extra = " | เทียบกับ " + json.dumps(t["rows_b"], ensure_ascii=False)[:40]
            print("%-4s %-8s %-44s -> %s%s"
                  % (c["id"], c["type"], c["question"][:44], head[:70], extra))
