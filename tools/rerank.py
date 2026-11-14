"""
ชั้นจัดอันดับด้วย cross-encoder ให้คะแนนความเกี่ยวข้องของคู่ข้อความโดยตรง

ต่างจาก bi-encoder ยังไง
    bi-encoder แปลงคำถามกับค่าเป็นเวกเตอร์แยกกัน แล้วค่อยวัดมุมระหว่างเวกเตอร์
    โมเดลไม่เคยเห็นสองข้อความพร้อมกัน คะแนนจึงกองกันอยู่แถวบนและแยกกันไม่ออก
    วัดจริงกับ multilingual-e5-small ได้ช่วงคะแนน 0.774 ถึง 0.873 ทั้งสเกล
    ระยะห่างระหว่างคำตอบถูกกับผิดเหลือราว 0.010 ซึ่งตั้งเกณฑ์ไม่ได้

    cross-encoder ใส่คำถามกับค่าเข้าโมเดลพร้อมกัน แล้วให้คะแนนออกมาตรง ๆ
    จึงแยกของที่เกี่ยวกับไม่เกี่ยวได้ชัดกว่ามาก

ทำไมใช้กับงานนี้ได้ทั้งที่ปกติมันช้า
    cross-encoder ต้องรันโมเดลครั้งละคู่ ถาม 1 ครั้งกับ 106 ค่าคือรัน 106 รอบ
    งาน RAG ทั่วไปมีเอกสารเป็นแสน จึงต้องคัดด้วยวิธีเร็วก่อนแล้วค่อยจัดอันดับ
    แต่คอลัมน์ที่เราใช้มีอย่างมาก 106 ค่า จึงให้คะแนนทุกค่าได้หมด
    ไม่มีขั้นคัดกรอง แปลว่าไม่มีคำตอบไหนหลุดก่อนถูกพิจารณา
"""

from __future__ import annotations

import math
import os
import threading

MODEL_NAME = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")

# เกณฑ์ชั่วคราว ต้องปรับจากคะแนนจริงหลังรัน selftest
# ตั้งไว้ก่อนตามพฤติกรรมทั่วไปของ bge-reranker ที่ให้ค่าใกล้ 1 เมื่อเกี่ยวข้องจริง
RERANK_FLOOR = float(os.environ.get("RERANK_FLOOR", "0.20"))
RERANK_HIGH = float(os.environ.get("RERANK_HIGH", "0.50"))
# ต้องนำอันดับสองห่างเท่านี้จึงจะใช้เลย ไม่งั้นถามกลับ
RERANK_MARGIN = float(os.environ.get("RERANK_MARGIN", "0.15"))

# กันไม่ให้เผลอจัดอันดับคอลัมน์ที่มีค่าเป็นพัน เพราะจะช้ามาก
MAX_PAIRS = int(os.environ.get("RERANK_MAX_PAIRS", "200"))

_model = None
_lock = threading.Lock()
_load_error: str | None = None


def _load():
    global _model, _load_error
    if _model is not None or _load_error is not None:
        return _model
    with _lock:
        if _model is None and _load_error is None:
            try:
                from sentence_transformers import CrossEncoder
                print("[rerank] กำลังโหลดโมเดล %s" % MODEL_NAME, flush=True)
                _model = CrossEncoder(MODEL_NAME, max_length=256)
                print("[rerank] โหลดโมเดลเสร็จ", flush=True)
            except Exception as e:
                _load_error = "%s: %s" % (type(e).__name__, e)
                print("[rerank] โหลดโมเดลไม่สำเร็จ %s" % _load_error, flush=True)
    return _model


def available() -> bool:
    return _load() is not None


def load_error() -> str | None:
    _load()
    return _load_error


def _to_prob(raw: list[float]) -> list[float]:
    """บีบคะแนนให้อยู่ระหว่าง 0 ถึง 1

    sentence-transformers บางรุ่นคืน logit ดิบ บางรุ่นใส่ sigmoid มาให้แล้ว
    จึงดูจากค่าที่ได้จริง ถ้ามีตัวไหนหลุดช่วง 0 ถึง 1 ถือว่าเป็น logit
    """
    if any(v < 0.0 or v > 1.0 for v in raw):
        return [1.0 / (1.0 + math.exp(-v)) for v in raw]
    return list(raw)


def score(field: str, values: list[str], phrase: str, top_k: int = 5):
    """ให้คะแนนทุกค่าในคอลัมน์ แล้วคืนอันดับต้น คืน None เมื่อใช้ชั้นนี้ไม่ได้"""
    if not values or not str(phrase).strip():
        return None
    if len(values) > MAX_PAIRS:
        return None
    m = _load()
    if m is None:
        return None
    raw = m.predict([(str(phrase), v) for v in values], batch_size=32,
                    show_progress_bar=False)
    probs = _to_prob([float(x) for x in raw])
    order = sorted(range(len(values)), key=lambda i: -probs[i])[:top_k]
    return [{"value": values[i], "score": round(probs[i], 4)} for i in order]


def selftest():
    """พิมพ์คะแนนจริงเพื่อใช้ตั้งเกณฑ์ ไม่ใช่เดาเอา"""
    from . import db

    if not available():
        print("ใช้ชั้นจัดอันดับไม่ได้:", load_error())
        return

    cases = [
        ("problem_detail", "เน็ตช้า"),
        ("problem_detail", "ความเร็วไม่ได้ตามที่โฆษณา"),
        ("problem_detail", "สัญญาณไม่ดี"),
        ("problem_group", "เน็ตช้า"),
        ("problem_group", "โดนคิดเงินเกิน"),
        ("problem_group", "ข้อความโฆษณากวน"),
        ("problem_group", "เสาส่งสัญญาณอันตรายต่อสุขภาพ"),
        ("problem_group", "ยกเลิกไม่ได้สักที"),
        ("problem_group", "ดาวอังคาร"),
        ("problem_group", "บริการ"),
        ("service_type", "มือถือ"),
        ("service_type", "เน็ตบ้าน"),
    ]
    con = db.connect()
    try:
        for field, phrase in cases:
            vals = [r[0] for r in con.execute(
                "select %s from complaints where %s is not null and %s <> '' group by %s"
                % (field, field, field, field))]
            hits = score(field, vals, phrase, top_k=3) or []
            print("\n%-16s %s   (%d ค่า)" % (field, phrase, len(vals)))
            for h in hits:
                print("    %-52s %.4f" % (h["value"][:52], h["score"]))
    finally:
        con.close()


if __name__ == "__main__":
    selftest()
