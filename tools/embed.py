"""
ชั้นเวกเตอร์ แปลงข้อความเป็นตัวเลขเพื่อค้นค่าด้วยความหมาย ไม่ใช่ตัวสะกด

ทำไมถึงต้องมี
  fuzzy matching เทียบตัวอักษร จึงจับได้แค่คำที่สะกดคล้ายกัน
  ผู้ใช้พิมพ์ "เน็ตช้า" แต่ค่าในฐานข้อมูลคือ "คุณภาพสัญญาณ"
  ตัวอักษรไม่ซ้ำกันเลยสักตัว fuzzy จึงให้คะแนนต่ำและตกไป
  เวกเตอร์จากโมเดลภาษาเทียบความหมาย จึงจับคู่แบบนี้ได้

ขนาดงาน
  ค่าที่ไม่ซ้ำทุกคอลัมน์รวมกันมีแค่ประมาณ 320 ค่า
  embed ครั้งเดียวเก็บไว้ในหน่วยความจำพอ ไม่ต้องติดตั้ง vector database แยก
  โมเดลถูกฝังไว้ใน image ตอน build จึงไม่ต้องต่ออินเทอร์เน็ตตอนรัน
"""

from __future__ import annotations

import os
import threading

MODEL_NAME = os.environ.get("EMBED_MODEL", "intfloat/multilingual-e5-small")

# โมเดลตระกูล e5 ถูกเทรนมาให้ใส่คำนำหน้าแยกว่าข้อความไหนเป็นคำค้น ข้อความไหนเป็นข้อมูล
# ถ้าไม่ใส่ คะแนนจะเพี้ยน ส่วนโมเดลตระกูลอื่นไม่ต้องใส่
_USE_PREFIX = "e5" in MODEL_NAME.lower()

_model = None
_lock = threading.Lock()
_cache: dict[str, tuple[list[str], object]] = {}
_load_error: str | None = None


def _load():
    """โหลดโมเดลครั้งแรกที่ถูกเรียก ครั้งต่อไปใช้ตัวเดิมในหน่วยความจำ"""
    global _model, _load_error
    if _model is not None or _load_error is not None:
        return _model
    with _lock:
        if _model is not None or _load_error is not None:
            return _model
        try:
            from sentence_transformers import SentenceTransformer
            print("[embed] กำลังโหลดโมเดล %s" % MODEL_NAME, flush=True)
            _model = SentenceTransformer(MODEL_NAME)
            print("[embed] โหลดโมเดลเสร็จ", flush=True)
        except Exception as e:
            _load_error = "%s: %s" % (type(e).__name__, e)
            print("[embed] โหลดโมเดลไม่สำเร็จ %s" % _load_error, flush=True)
    return _model


def available() -> bool:
    """บอกว่าชั้นเวกเตอร์ใช้งานได้ไหม ถ้าไม่ได้ resolve จะถอยไปใช้ fuzzy เอง"""
    return _load() is not None


def load_error() -> str | None:
    _load()
    return _load_error


def _encode(texts: list[str], kind: str):
    m = _load()
    if m is None:
        return None
    if _USE_PREFIX:
        texts = ["%s: %s" % (kind, t) for t in texts]
    return m.encode(texts, normalize_embeddings=True, show_progress_bar=False)


def field_matrix(field: str, values: list[str]):
    """embed ค่าทั้งหมดของคอลัมน์หนึ่ง แล้วเก็บไว้ใช้ซ้ำ

    key ของ cache ผูกกับจำนวนค่าด้วย เผื่อฐานข้อมูลถูกสร้างใหม่แล้วค่าเปลี่ยน
    """
    key = "%s|%d" % (field, len(values))
    hit = _cache.get(key)
    if hit is not None:
        return hit
    mat = _encode(list(values), "passage")
    if mat is None:
        return None
    _cache[key] = (list(values), mat)
    return _cache[key]


def search(field: str, values: list[str], phrase: str, top_k: int = 5):
    """คืนค่าที่ใกล้เคียงที่สุดพร้อมคะแนน cosine ระหว่าง 0 ถึง 1

    คืน None เมื่อชั้นเวกเตอร์ใช้ไม่ได้ ผู้เรียกต้องถอยไปใช้วิธีเดิม
    """
    if not values or not str(phrase).strip():
        return None
    packed = field_matrix(field, values)
    if packed is None:
        return None
    vals, mat = packed
    q = _encode([str(phrase)], "query")
    if q is None:
        return None
    import numpy as np

    scores = (mat @ q[0]).tolist()
    order = sorted(range(len(vals)), key=lambda i: -scores[i])[:top_k]
    return [{"value": vals[i], "score": round(float(scores[i]), 4)} for i in order]


def selftest():
    """ทดสอบด้วยค่าจริงจากฐานข้อมูล ดูว่าโมเดลจับความหมายภาษาไทยได้จริงไหม"""
    from . import db

    if not available():
        print("ใช้ชั้นเวกเตอร์ไม่ได้:", load_error())
        return

    cases = [
        ("problem_group", "เน็ตช้า"),
        ("problem_group", "โดนคิดเงินเกิน"),
        ("problem_group", "ยกเลิกไม่ได้สักที"),
        ("problem_group", "เสาสัญญาณใกล้บ้านอันตรายไหม"),
        ("problem_group", "ข้อความโฆษณากวน"),
        ("problem_group", "สัญญาณหลุดบ่อย"),
        ("problem_group", "ดาวอังคาร"),
        ("service_type", "มือถือ"),
        ("service_type", "เน็ตบ้าน"),
        ("province", "เมืองกรุง"),
    ]
    con = db.connect()
    try:
        for field, phrase in cases:
            vals = [
                r[0]
                for r in con.execute(
                    "select %s from complaints where %s is not null and %s <> '' group by %s"
                    % (field, field, field, field)
                )
            ]
            hits = search(field, vals, phrase, top_k=3) or []
            print(
                "%-14s %-26s" % (field, phrase),
                " | ".join("%s %.3f" % (h["value"], h["score"]) for h in hits),
            )
    finally:
        con.close()


if __name__ == "__main__":
    selftest()
