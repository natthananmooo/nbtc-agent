"""
eval/score.py - คิดคะแนนสองแกน ด้วยสูตรตายตัว ไม่ใช้ LLM ตัดสิน

ทำไมไม่ใช้ LLM เป็นกรรมการ
  ใช้ LLM ตัดสินจะได้คะแนนที่อธิบายไม่ได้ และเปลี่ยนไปทุกครั้งที่รัน
  สองแกนนี้คำนวณตรง ๆ ได้ จึงไม่จำเป็นต้องใช้

correctness   คำตอบตรงกับเฉลยที่คำนวณจาก SQL ไหม
              นับจากค่าที่ต้องปรากฏ และชื่อกลุ่มที่ต้องปรากฏ
              ชนิด clarify ถูกต้องคือ "ถามกลับ" ไม่ใช่ตอบตัวเลข

faithfulness  ตัวเลขทุกตัวในคำตอบ มาจากผลลัพธ์ของ tool จริงไหม
              = จำนวนตัวเลขที่หาเจอใน context / จำนวนตัวเลขทั้งหมดในคำตอบ
              ถ้าโมเดลเติมตัวเลขเอง คะแนนจะตก
"""

from __future__ import annotations

import json
import re

NUM = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

# คำที่บอกว่ากำลังถามกลับ
ASK_MARKERS = ("?", "หมายถึง", "แบบไหน", "ช่วยระบุ", "อยากทราบว่า", "ต้องการ",
               "ระบุให้ชัด", "หรือไม่ครับ", "ไหนครับ", "ไหนคะ", "โปรดระบุ")

# คำที่บอกว่ากำลังปฏิเสธเพราะไม่มีข้อมูล ถือว่าถูกต้องสำหรับชนิด clarify
NO_DATA_MARKERS = ("ไม่มีข้อมูล", "ไม่พบข้อมูล", "ไม่มีค่านี้", "ตอบไม่ได้",
                   "ไม่สามารถตอบ", "ฐานข้อมูลไม่มี")


def numbers(text: str) -> list[float]:
    out = []
    for m in NUM.findall(text or ""):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    return out


def _close(a: float, b: float) -> bool:
    """
    จำนวนนับต้องตรงเป๊ะ เพราะ 798 กับ 800 คือคนละคำตอบ
    ยอมให้ต่างได้เฉพาะค่าที่มีทศนิยม เช่น 70.54 กับ 70.5 ที่เป็นเรื่องการปัดเศษ
    """
    if a == int(a) and b == int(b):
        return a == b
    return abs(a - b) <= max(0.05, abs(a) * 0.01)


def _found(value: float, pool: list[float]) -> bool:
    return any(_close(value, p) for p in pool)


# ------------------------------------------------------------- faithfulness
# เลขข้อในรายการ ไม่ใช่การอ้างตัวเลข
# คำตอบแบบ "1. กรุงเทพมหานคร 798" เลข 1 เป็นลำดับ ส่วน 798 คือสิ่งที่ต้องตรวจ
LIST_MARKER = re.compile(r"(?m)^\s*(\d{1,2})[\.\)]\s")


def _strip_list_markers(text: str) -> str:
    return LIST_MARKER.sub(" ", text or "")


def faithfulness(answer: str, context: list[dict],
                 schema: str = "") -> tuple[float, list[float]]:
    """คืน (คะแนน, รายการตัวเลขที่หาที่มาไม่ได้)

    context  ผลลัพธ์ที่ได้จาก tool ในรอบนี้
    schema   คำอธิบายฐานข้อมูลที่ส่งให้โมเดลผ่าน MCP resource
             ต้องนับเป็นข้อมูลที่ดึงมาด้วย เพราะโมเดลไม่ได้คิดขึ้นเอง
             เช่นประโยคว่าข้อมูลมีตั้งแต่ปี 2552 ถึง 2569 เป็นความจริงที่อ่านมาจาก schema
             ตรวจแล้วว่า schema ไม่มีจำนวนเรื่องร้องเรียนอยู่เลย
             การนับเข้าไปจึงไม่ทำให้ตัวเลขที่แต่งขึ้นหลุดรอด
    """
    blob = "\n".join(json.dumps(c, ensure_ascii=False) if isinstance(c, dict) else str(c)
                     for c in context)
    pool = numbers(blob + "\n" + (schema or ""))
    claimed = numbers(_strip_list_markers(answer))
    if not claimed:
        return 1.0, []          # ไม่มีตัวเลขให้ผิด
    bad = [n for n in claimed if not _found(n, pool)]
    return round(1 - len(bad) / len(claimed), 4), bad


# -------------------------------------------------------------- correctness
def _expected(truth: dict) -> tuple[list[float], list[str]]:
    """ดึงค่าตัวเลขและชื่อกลุ่มที่คำตอบต้องกล่าวถึง"""
    values: list[float] = []
    labels: list[str] = []
    rows = truth["rows"][: truth.get("top_n", 1)]
    for r in rows:
        for k, v in r.items():
            if k == "n":
                continue
            if isinstance(v, (int, float)):
                values.append(float(v))
            elif isinstance(v, str):
                labels.append(v)
    for r in truth.get("rows_b", [])[: truth.get("top_n", 1)]:
        for k, v in r.items():
            if k != "n" and isinstance(v, (int, float)):
                values.append(float(v))
    return values, labels


def correctness(case: dict, answer: str, truth: dict) -> tuple[float, dict]:
    answer = answer or ""

    if truth["kind"] == "clarify":
        asked = any(m in answer for m in ASK_MARKERS)
        refused = any(m in answer for m in NO_DATA_MARKERS)
        ok = asked or refused
        return (1.0 if ok else 0.0), {
            "ถามกลับ": asked, "บอกว่าไม่มีข้อมูล": refused,
            "เหตุผลที่ต้องถาม": case.get("why", "")}

    values, labels = _expected(truth)
    pool = numbers(answer)

    hit_v = [v for v in values if _found(v, pool)]
    hit_l = [l for l in labels if l in answer]

    parts = []
    if values:
        parts.append(len(hit_v) / len(values))
    if labels:
        parts.append(len(hit_l) / len(labels))
    score = sum(parts) / len(parts) if parts else 0.0

    return round(score, 4), {
        "ค่าที่ต้องมี": values, "เจอ": hit_v,
        "ชื่อกลุ่มที่ต้องมี": labels, "เจอชื่อ": hit_l,
    }


# ------------------------------------------- คำอธิบายที่มาของคะแนน
# ข้อความพวกนี้ถูกส่งไปกับคะแนนใน Langfuse เพื่อให้เปิดดูแล้วคำนวณตามได้
# ทีละขั้นโดยไม่ต้องเปิดโค้ด ตอบคำถามว่า "คะแนนนี้มาจากไหน"


def _brief(seq, n: int = 12) -> str:
    """ตัดรายการยาวให้สั้นลง กัน comment ยาวเกินที่ Langfuse รับ"""
    seq = list(seq or [])
    if len(seq) <= n:
        return str(seq)
    return "%s ... อีก %d ตัว" % (seq[:n], len(seq) - n)


def explain_correctness(truth: dict, score: float, detail: dict) -> str:
    if truth["kind"] == "clarify":
        return (
            "correctness ชนิด clarify\n"
            "เกณฑ์ ถูกเมื่อถามกลับ หรือบอกตรง ๆ ว่าไม่มีข้อมูล ไม่ใช่ตอบเป็นตัวเลข\n"
            "ถามกลับ = %s\n"
            "บอกว่าไม่มีข้อมูล = %s\n"
            "เหตุผลที่คำถามนี้ต้องถามกลับ %s\n"
            "คะแนน = %.4f"
            % (detail.get("ถามกลับ"), detail.get("บอกว่าไม่มีข้อมูล"),
               detail.get("เหตุผลที่ต้องถาม", ""), score))

    values, hit_v = detail.get("ค่าที่ต้องมี", []), detail.get("เจอ", [])
    labels, hit_l = detail.get("ชื่อกลุ่มที่ต้องมี", []), detail.get("เจอชื่อ", [])
    parts = []
    if values:
        parts.append("ตัวเลข %d/%d" % (len(hit_v), len(values)))
    if labels:
        parts.append("ชื่อกลุ่ม %d/%d" % (len(hit_l), len(labels)))
    return (
        "correctness เทียบกับเฉลยที่คำนวณจาก SQL ตรง ๆ ไม่มี LLM ตัดสิน\n"
        "สูตร เฉลี่ยของสัดส่วนที่เจอ = %s\n"
        "ค่าที่ต้องมี %s\n"
        "เจอในคำตอบ %s\n"
        "ชื่อกลุ่มที่ต้องมี %s\n"
        "เจอในคำตอบ %s\n"
        "คะแนน = %.4f"
        % (" และ ".join(parts) or "(ไม่มีเกณฑ์)", _brief(values), _brief(hit_v),
           _brief(labels), _brief(hit_l), score))


def explain_faithfulness(answer: str, context: list[dict], schema: str,
                         score: float, unsupported: list[float]) -> str:
    claimed = numbers(_strip_list_markers(answer))
    if not claimed:
        return "faithfulness = 1.0000 เพราะคำตอบไม่มีตัวเลขให้ตรวจ"
    return (
        "faithfulness ตรวจว่าตัวเลขทุกตัวในคำตอบมาจากข้อมูลที่ดึงมาจริง\n"
        "สูตร 1 - (ตัวเลขที่หาที่มาไม่ได้ / ตัวเลขทั้งหมดในคำตอบ)\n"
        "ตัวเลขที่คำตอบอ้าง %d ตัว %s\n"
        "ที่หาที่มาไม่ได้ %d ตัว %s\n"
        "แหล่งอ้างอิง ผลของ tool %d รายการ บวก schema จาก MCP resource\n"
        "เลขลำดับข้อแบบ 1. 2. ถูกตัดทิ้งก่อนนับ ไม่ถือเป็นตัวเลขที่อ้าง\n"
        "คะแนน = 1 - %d/%d = %.4f"
        % (len(claimed), _brief(claimed), len(unsupported), _brief(unsupported),
           len(context or []), len(unsupported), len(claimed), score))


if __name__ == "__main__":
    ctx = [{"tool": "query_complaints",
            "output": json.dumps({"rows": [{"province": "กรุงเทพมหานคร", "count": 798},
                                           {"province": "นนทบุรี", "count": 172}],
                                  "sql_used": "SELECT ..."}, ensure_ascii=False)}]
    truth = {"kind": "rows", "top_n": 2,
             "rows": [{"province": "กรุงเทพมหานคร", "count": 798},
                      {"province": "นนทบุรี", "count": 172}]}

    tests = [
        ("ตอบถูกครบ", "กรุงเทพมหานคร 798 เรื่อง รองลงมานนทบุรี 172 เรื่อง"),
        ("ตอบถูกครึ่งเดียว", "กรุงเทพมหานคร 798 เรื่อง"),
        ("เดาตัวเลขเพิ่ม", "กรุงเทพมหานคร 798 เรื่อง นนทบุรี 172 เรื่อง และเชียงใหม่ 999 เรื่อง"),
        ("ตอบถูกแต่เดาล้วน", "กรุงเทพมหานคร 800 เรื่อง"),
    ]
    for name, ans in tests:
        c, _ = correctness({}, ans, truth)
        f, bad = faithfulness(ans, ctx)
        print("%-18s correctness %.2f  faithfulness %.2f  %s"
              % (name, c, f, ("ตัวเลขไม่มีที่มา: %s" % bad) if bad else ""))

    print()
    for name, ans in [("ถามกลับ", "หมายถึงกลุ่มปัญหาไหนครับ"),
                      ("บอกว่าไม่มี", "ไม่มีข้อมูลนี้ในฐานข้อมูล"),
                      ("เดาไปเลย", "มี 1,234 เรื่องครับ")]:
        c, d = correctness({"why": "x"}, ans, {"kind": "clarify"})
        print("clarify %-12s correctness %.2f  %s" % (name, c, d))
