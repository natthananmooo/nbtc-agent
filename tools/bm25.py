"""
ชั้นค้นด้วยคำ ตัดคำไทยก่อนแล้วให้คะแนนด้วย BM25

ทำไมถึงต้องมี ทั้งที่มีการเทียบตัวอักษรอยู่แล้ว
    ภาษาไทยไม่เว้นวรรค SequenceMatcher จึงเทียบตัวอักษรดิบติดกันทั้งพรืด
    ผู้ใช้พิมพ์ "สัญญาณไม่ดี" ค่าจริงคือ "คุณภาพสัญญาณ" มีคำว่าสัญญาณซ้ำกันชัด ๆ
    แต่คะแนนตัวอักษรออกมา 0.52 ต่ำกว่าเกณฑ์ ตกไปทั้งที่ควรจับได้
    พอตัดคำก่อนแล้วนับคำซ้ำ กรณีแบบนี้จับได้หมด

ขอบเขตของวิธีนี้
    BM25 นับคำที่ซ้ำกัน ไม่ได้เข้าใจความหมาย
    "เน็ตช้า" ตัดได้ เน็ต|ช้า ซึ่งไม่โผล่ในค่าไหนเลย คะแนนจึงเป็นศูนย์จริง ๆ
    กรณีที่ไม่มีคำซ้ำกันเลยเป็นงานของชั้นเวกเตอร์ ไม่ใช่ของชั้นนี้
"""

from __future__ import annotations

import os
import threading

# เกณฑ์ตั้งจากการวัดกับค่าจริงในคอลัมน์ problem_group
#   คำกว้าง ๆ ที่ไม่ควรรับ เช่น "บริการ" "ปัญหา" ได้ 1.37 ถึง 1.76
#   คำที่จับถูกจริง ได้ 2.28 ถึง 6.29
# จึงตัดที่ 2.0 ซึ่งแยกสองกลุ่มนี้ออกจากกันพอดี
BM25_FLOOR = float(os.environ.get("BM25_FLOOR", "2.0"))
BM25_HIGH = float(os.environ.get("BM25_HIGH", "2.5"))
# อันดับหนึ่งต้องนำอันดับสองเท่านี้จึงจะใช้เลย ไม่งั้นให้ถามกลับ
# ตั้ง 1.8 เพราะที่วัดมา คู่ที่อันดับหนึ่งผิดมักห่างกันแค่ 1.2 ถึง 1.5
BM25_RATIO = float(os.environ.get("BM25_RATIO", "1.8"))

_cache: dict[str, tuple[list[str], object]] = {}
_lock = threading.Lock()
_load_error: str | None = None
_tokenize = None


def _tok():
    global _tokenize, _load_error
    if _tokenize is not None or _load_error is not None:
        return _tokenize
    with _lock:
        if _tokenize is None and _load_error is None:
            try:
                from pythainlp.tokenize import word_tokenize
                _tokenize = lambda s: word_tokenize(str(s), engine="newmm")  # noqa: E731
            except Exception as e:
                _load_error = "%s: %s" % (type(e).__name__, e)
                print("[bm25] ใช้การตัดคำไม่ได้ %s" % _load_error, flush=True)
    return _tokenize


def available() -> bool:
    return _tok() is not None


def load_error() -> str | None:
    _tok()
    return _load_error


def _index(field: str, values: list[str]):
    key = "%s|%d" % (field, len(values))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    tk = _tok()
    if tk is None:
        return None
    try:
        from rank_bm25 import BM25Okapi
    except Exception as e:
        print("[bm25] ไม่มี rank_bm25 %s" % e, flush=True)
        return None
    _cache[key] = (list(values), BM25Okapi([tk(v) for v in values]))
    return _cache[key]


def search(field: str, values: list[str], phrase: str, top_k: int = 5):
    """คืนค่าที่คำซ้ำกันมากที่สุดพร้อมคะแนน BM25 คืน None เมื่อใช้ชั้นนี้ไม่ได้"""
    if not values or not str(phrase).strip():
        return None
    packed = _index(field, values)
    if packed is None:
        return None
    vals, bm = packed
    scores = bm.get_scores(_tok()(phrase))
    order = sorted(range(len(vals)), key=lambda i: -scores[i])[:top_k]
    out = [{"value": vals[i], "score": round(float(scores[i]), 4)} for i in order]
    return [h for h in out if h["score"] > 0]


def selftest():
    from . import db

    if not available():
        print("ใช้ชั้น BM25 ไม่ได้:", load_error())
        return
    cases = [
        ("problem_group", "สัญญาณไม่ดี"),
        ("problem_group", "สัญญาณหลุดบ่อย"),
        ("problem_group", "ข้อความโฆษณากวน"),
        ("problem_group", "เสาส่งสัญญาณอันตรายต่อสุขภาพ"),
        ("problem_group", "เน็ตช้า"),
        ("problem_group", "บริการ"),
        ("province", "ดาวอังคาร"),
    ]
    con = db.connect()
    try:
        for field, phrase in cases:
            vals = [r[0] for r in con.execute(
                "select %s from complaints where %s is not null and %s <> '' group by %s"
                % (field, field, field, field))]
            hits = search(field, vals, phrase, top_k=2) or []
            print("%-14s %-30s" % (field, phrase),
                  " | ".join("%s %.2f" % (h["value"][:34], h["score"]) for h in hits)
                  or "ไม่มีคำซ้ำกันเลย")
    finally:
        con.close()


if __name__ == "__main__":
    selftest()
