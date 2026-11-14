"""
tools/resolve.py - แปลงคำที่ผู้ใช้พิมพ์ ให้เป็นค่าที่มีอยู่จริงในฐานข้อมูล

ทำไมต้องมี tool ตัวนี้
    ผู้ใช้พิมพ์ "AIS" แต่ในฐานข้อมูลไม่มีคำว่า AIS อยู่เลย
    มีแต่ "บริษัท แอดวานซ์ ไวร์เลส เน็ทเวอร์ค จำกัด"
    ถ้า agent เดาเอง SQL จะถูก syntax แต่ได้คำตอบผิด ซึ่งจับยากมาก

ค่าที่คืนกลับมีฟิลด์ confidence สามค่า
    high       ชัดเจน ใช้ได้เลย
    ambiguous  ตรงได้หลายทาง agent ต้องถามกลับ ไม่ใช่เดา
    none       ไม่มีค่านี้ในฐานข้อมูล agent ต้องบอกตรง ๆ
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import yaml

from . import db, embed

ALIAS_FILE = Path(__file__).resolve().parent.parent / "aliases.yaml"

# คอลัมน์ที่ค้นด้วยข้อความได้ (year กับ month เป็นตัวเลข ไม่ต้องแปลง)
SEARCHABLE = (
    "provider", "service_type", "problem_group", "problem_detail",
    "region", "province", "district", "channel", "status",
    "cat_level1", "cat_level2",
)

MAX_CANDIDATES = 5
MAX_MENU = 30   # จำนวนค่าสูงสุดที่ส่งกลับเป็นเมนูให้ผู้ใช้เลือก

# เกณฑ์คะแนน แยกสองระดับเพราะหลักฐานไม่เท่ากัน
#   substring  คำที่พิมพ์โผล่อยู่ในค่าจริง เป็นหลักฐานตรง เชื่อได้
#   fuzzy      คะแนนความคล้ายตัวอักษร ภาษาไทยให้ค่าหลอกได้ง่าย ต้องตั้งสูง
MIN_FUZZY = 0.78
MIN_SUBSTRING = 0.70

# เกณฑ์ของชั้นเวกเตอร์ แยกจากเกณฑ์ตัวอักษรเพราะคะแนนคนละสเกลกัน
# โมเดล e5 ให้คะแนนพื้นฐานสูงอยู่แล้ว ข้อความที่ไม่เกี่ยวกันเลยก็ยังได้ราว 0.75
# จึงต้องตั้งพื้นสูงและดูระยะห่างระหว่างอันดับหนึ่งกับอันดับสองด้วย
VEC_FLOOR = float(os.environ.get("VEC_FLOOR", "0.82"))   # ต่ำกว่านี้ถือว่าไม่เกี่ยว
VEC_HIGH = float(os.environ.get("VEC_HIGH", "0.88"))     # สูงกว่านี้จึงจะใช้เลยได้
VEC_GAP = float(os.environ.get("VEC_GAP", "0.03"))       # ต้องนำอันดับสองเท่านี้
VEC_TOP = 3


def _rag_on() -> bool:
    """เปิดปิดชั้นเวกเตอร์ด้วย env เพื่อให้วัดผลเทียบกันได้ว่ามี RAG แล้วดีขึ้นจริงไหม

    อ่านทุกครั้งที่เรียก ไม่ใช่ตอน import จะได้เปลี่ยนค่าแล้วรีสตาร์ตอย่างเดียวก็พอ
    """
    return os.environ.get("RAG_MODE", "off").strip().lower() in ("1", "on", "true", "yes")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


@lru_cache(maxsize=1)
def _aliases() -> dict[str, dict[str, list[str]]]:
    """อ่าน brand_aliases.yaml ไฟล์ที่คนเขียน ไม่ใช่ AI เดา"""
    if not ALIAS_FILE.exists():
        return {}
    data = yaml.safe_load(ALIAS_FILE.read_text(encoding="utf-8")) or {}
    return {f: {_norm(k): v for k, v in groups.items()}
            for f, groups in data.items() if groups}


@lru_cache(maxsize=32)
def _values(field: str) -> tuple[tuple[str, int], ...]:
    return tuple(db.distinct_values(field))


def _similarity(phrase: str, value: str) -> tuple[float, str]:
    """
    คืน (คะแนน, ชนิดหลักฐาน) ชนิดเป็น exact, substring หรือ fuzzy
    ไม่ใช้ embedding ตามขอบเขต MVP แต่เปลี่ยนมาใช้ได้ภายหลังโดยไม่แตะที่อื่น
    """
    p, v = _norm(phrase), _norm(value)
    if p == v:
        return 1.0, "exact"
    if p in v:
        # คำที่พิมพ์โผล่อยู่ในค่าจริง ยิ่งกินสัดส่วนมากยิ่งมั่นใจ
        return 0.70 + 0.25 * (len(p) / len(v)), "substring"
    if v in p:
        return 0.70 + 0.20 * (len(v) / len(p)), "substring"
    return SequenceMatcher(None, p, v).ratio(), "fuzzy"


def resolve_filter_values(user_phrase: str, field: str | None = None) -> dict:
    """
    แปลงคำผู้ใช้เป็นค่าในฐานข้อมูล

    user_phrase : คำที่ผู้ใช้พิมพ์ เช่น "AIS" "เน็ตช้า" "ภาคเหนือ"
    field       : ระบุคอลัมน์ หรือเว้นไว้ให้ค้นทุกคอลัมน์
    """
    phrase = _norm(user_phrase)

    # year กับ month เป็นตัวเลข ไม่ต้องแปลงอะไร ส่งเข้า filters ได้เลย
    if field in ("year", "month"):
        # ไม่ใช่ "ไม่พบ" แต่เป็น "ไม่ต้องแปลง" คนละความหมายกัน
        # แถมค่าที่พร้อมใช้ไปเลย โมเดลจะได้ไม่ต้องเรียกซ้ำ
        digits = re.sub(r"[^\d]", "", str(user_phrase))
        ready = {field: [int(digits)]} if digits else None
        return {
            "query": user_phrase, "field": field, "candidates": [],
            "use_in_filters": ready,
            "confidence": "not_needed", "alias_group": None,
            "method": "numeric",
            "note": "คอลัมน์ %s เป็นตัวเลข ไม่ต้องแปลงอะไร "
                    "ใส่ใน filters ของ query_complaints ได้เลย "
                    "ฐานข้อมูลเก็บปีเป็น พ.ศ. ตามต้นฉบับ" % field,
        }

    fields = [db.check_dimension(field)] if field else list(SEARCHABLE)

    # --- 1. ดูไฟล์ alias ที่คนเขียนไว้ก่อน ---
    for f in fields:
        group = _aliases().get(f, {}).get(phrase)
        if group:
            counts = dict(_values(f))
            members = [{"value": v, "score": 1.0, "rows_all_years": counts.get(v, 0)}
                       for v in group if v in counts]
            return {
                "query": user_phrase,
                "field": f,
                "candidates": members,
                # รายการที่ต้องใส่ใน filters ทั้งชุด ห้ามหยิบไปแค่ตัวเดียว
                "use_in_filters": {f: [m["value"] for m in members]},
                "confidence": "high",
                "alias_group": user_phrase,
                "method": "alias",
                "note": "มาจาก aliases.yaml รวม %d รายการ "
                        "ต้องแจ้งผู้ใช้ว่ารวมอะไรเข้าด้วยกันบ้าง "
                        "ตัวเลข rows คือยอดรวมทุกปีของค่านั้นในฐานข้อมูล "
                        "ไม่ใช่คำตอบของคำถาม ห้ามเอาไปบวกกันเองแล้วตอบ "
                        "ต้องเรียก query_complaints ด้วย use_in_filters เสมอ"
                        % len(members),
            }

    # --- 2. ค้นจากค่าจริงในฐานข้อมูล ---
    scored = []
    for f in fields:
        for value, rows in _values(f):
            s, kind = _similarity(phrase, value)
            floor = MIN_FUZZY if kind == "fuzzy" else MIN_SUBSTRING
            if s >= floor:
                scored.append((s, f, value, rows, kind))
    scored.sort(key=lambda t: (-t[0], -t[3]))

    # --- 2.5 ชั้นเวกเตอร์ ใช้เมื่อหลักฐานตัวอักษรไม่หนักแน่น ---
    # ถ้าคำที่พิมพ์ตรงตัวหรือเป็นคำย่อยของค่าจริงอยู่แล้ว วิธีเดิมแม่นและอธิบายได้
    # ชั้นเวกเตอร์มีไว้สำหรับกรณีที่ตัวอักษรไม่ซ้ำกันเลย เช่น เน็ตช้า กับ คุณภาพสัญญาณ
    lexical_solid = bool(scored) and scored[0][4] in ("exact", "substring")
    vector_used = False
    if _rag_on() and not lexical_solid and embed.available():
        vec = []
        for f in fields:
            pairs = _values(f)
            hits = embed.search(f, [v for v, _ in pairs], user_phrase, top_k=VEC_TOP) or []
            counts = dict(pairs)
            for h in hits:
                if h["score"] >= VEC_FLOOR:
                    vec.append((h["score"], f, h["value"], counts.get(h["value"], 0), "vector"))
        if vec:
            vec.sort(key=lambda t: (-t[0], -t[3]))
            scored = vec
            vector_used = True

    if not scored:
        # ไม่เจอค่าที่ตรง แต่ต้องช่วยให้ถามกลับได้อย่างมีข้อมูล
        # ส่งเมนูค่าจริงของคอลัมน์นั้นกลับไปพร้อมจำนวนเรื่อง
        # กันไม่ให้โมเดลไปงมค่าจาก prompt แล้วหยิบข้ามคอลัมน์มา
        menu, menu_note = [], ""
        if field:
            vals = _values(field)
            menu = [{"value": v, "rows_all_years": n} for v, n in vals[:MAX_MENU]]
            if len(vals) > MAX_MENU:
                menu_note = " คอลัมน์นี้มี %d ค่า แสดงแค่ %d ค่าที่พบบ่อยที่สุด" % (
                    len(vals), MAX_MENU)
        return {
            "query": user_phrase,
            "field": field,
            "candidates": [],
            "use_in_filters": None,
            "confidence": "none",
            "alias_group": None,
            "method": "vector" if vector_used else "lexical",
            "field_values": menu,
            "note": ("ไม่มีค่าไหนในคอลัมน์ %s ที่ตรงกับคำนี้ ห้ามเดาเด็ดขาด "
                     "ให้ถามผู้ใช้โดยเสนอเฉพาะค่าจาก field_values ข้างล่างนี้เท่านั้น "
                     "พร้อมบอกจำนวนเรื่องของแต่ละตัว ห้ามเสนอค่าจากคอลัมน์อื่น%s"
                     % (field or "ใดเลย", menu_note)) if field else
                    ("ไม่มีค่าไหนตรงพอในทุกคอลัมน์ ห้ามเดา "
                     "ให้ถามผู้ใช้ว่าหมายถึงอะไร หรือระบุคอลัมน์มาแล้วเรียกใหม่ "
                     "เพื่อให้ได้รายการค่าที่มีจริงไปเสนอ"),
        }

    top_field = scored[0][1]
    same = [t for t in scored if t[1] == top_field][:MAX_CANDIDATES]
    cands = [{"value": v, "score": round(s, 3), "rows_all_years": n, "evidence": k}
             for s, _, v, n, k in same]

    # --- 3. ตัดสิน confidence ---
    if vector_used:
        # จับคู่ด้วยความหมาย ไม่มีหลักฐานตัวอักษรรองรับ จึงต้องเข้มกว่าปกติ
        # ถ้าไม่ขาดลอยจริง ให้ถามกลับ ดีกว่าเดาแล้วได้ SQL ที่ถูก syntax แต่ผิดความหมาย
        clear = cands[0]["score"] >= VEC_HIGH and (
            len(cands) == 1 or cands[0]["score"] - cands[1]["score"] >= VEC_GAP
        )
        if clear:
            conf, note = "high", (
                "ไม่มีคำไหนตรงตัว แต่จับคู่ได้จากความหมาย ใช้ค่านี้ได้ "
                "ต้องบอกผู้ใช้ให้ชัดว่าตีความคำของเขาเป็นค่าอะไรในฐานข้อมูล "
                "ตัวเลข rows คือยอดรวมทุกปี ไม่ใช่คำตอบ ต้องเรียก query_complaints ต่อเสมอ")
        else:
            conf, note = "ambiguous", (
                "จับคู่ด้วยความหมายแล้วได้หลายค่าที่ใกล้เคียงกัน ห้ามเดา "
                "ให้ถามผู้ใช้ว่าหมายถึงอันไหน พร้อมบอกจำนวนเรื่องของแต่ละตัวเลือก")
    elif cands[0]["score"] >= 0.85 and (
        len(cands) == 1 or cands[0]["score"] - cands[1]["score"] >= 0.15
    ):
        conf, note = "high", ("ตรงชัดเจน ใช้ค่านี้ได้ และบอกผู้ใช้ว่าใช้ค่าอะไร "
                              "ตัวเลข rows คือยอดรวมทุกปีของค่านั้น ไม่ใช่คำตอบของคำถาม "
                              "ต้องเรียก query_complaints ต่อเสมอ")
    else:
        conf, note = "ambiguous", (
            "ตรงได้หลายค่า ต้องถามผู้ใช้ว่าหมายถึงอันไหน "
            "พร้อมบอกจำนวนเรื่องของแต่ละตัวเลือก ห้ามเดา")

    return {
        "query": user_phrase,
        "field": top_field,
        "candidates": cands,
        # ใส่ให้เฉพาะตอนชัดเจน ถ้ากำกวมต้องถามผู้ใช้ก่อน ไม่ใช่หยิบไปใช้เอง
        "use_in_filters": ({top_field: [cands[0]["value"]]} if conf == "high" else None),
        "confidence": conf,
        "alias_group": None,
        "method": "vector" if vector_used else "lexical",
        "note": note,
    }


def values_for_prompt(fields: tuple = SEARCHABLE, limit: int = 120) -> str:
    """
    รายการค่าไม่ซ้ำ สำหรับใส่ใน system prompt ตามขอบเขต MVP
    (district มี 861 ค่า จึงตัดออก ให้ใช้ resolve_filter_values แทน)
    """
    out = []
    for f in fields:
        vals = _values(f)
        if len(vals) > limit:
            out.append("%s: มี %d ค่า มากเกินกว่าจะใส่ทั้งหมด ให้เรียก resolve_filter_values"
                       % (f, len(vals)))
            continue
        out.append("%s (%d): %s" % (f, len(vals), " | ".join(v for v, _ in vals)))
    return "\n".join(out)


if __name__ == "__main__":
    for q in ["AIS", "ทรู", "เน็ตช้า", "ภาคเหนือ", "มือถือ", "เชียงใหม่", "ดาวอังคาร"]:
        r = resolve_filter_values(q)
        print("\n%-10s -> %-14s %s" % (q, r["field"], r["confidence"]))
        if r["alias_group"]:
            print("            alias:", r["note"].split(" ต้อง")[0])
        for c in r["candidates"][:4]:
            print("            %.2f  %6d  %s" % (c["score"], c["rows_all_years"], c["value"]))
