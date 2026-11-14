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

from . import bm25, db, rerank

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

VEC_TOP = 3

# คอลัมน์ที่ให้ชั้น bm25 กับชั้นจัดอันดับทำงาน
# เฉพาะคอลัมน์ที่ค่าเป็นคำบรรยายซึ่งมีความหมายให้เทียบ
#
# ชื่อเฉพาะอย่างจังหวัด อำเภอ และชื่อนิติบุคคล ต้องกันออกทั้งสองชั้น
#   วัดด้วย bi-encoder  "เมืองกรุง" ได้ นครนายก 0.808 แซง กรุงเทพมหานคร 0.807
#   วัดด้วย bm25        "เน็ตช้า" ตัดได้ เน็ต|ช้า ไปชนคำว่าเน็ตใน ไตรเน็ต
#                       แล้วตอบ บริษัท ดีแทค ไตรเน็ต จำกัด แบบมั่นใจเต็มที่
# ชื่อองค์กรต้องมาจาก alias ที่คนเขียน หรือจากการที่คำนั้นอยู่ในชื่อจริง ๆ เท่านั้น
#
# อีกเหตุผลคือ cross-encoder ต้องรันครั้งละคู่ คอลัมน์ที่มีค่าเป็นร้อยเป็นพันจะช้ามาก
RAG_FIELDS = ("problem_group", "problem_detail", "service_type",
              "cat_level1", "cat_level2", "channel", "status")


def _mode() -> str:
    """โหมดการค้นค่า อ่านจาก env ทุกครั้งที่เรียก ไม่ใช่ตอน import

    off     เทียบตัวอักษรอย่างเดียว เป็นฐานสำหรับเปรียบเทียบ
    bm25    เพิ่มการตัดคำไทยแล้วนับคำซ้ำ
    rerank  เพิ่มการให้คะแนนด้วย cross-encoder ต่อจาก bm25

    แยกสามระดับเพื่อให้วัดได้ว่าโมเดลที่หนักกว่ามาก คุ้มกับที่จ่ายไปไหม
    """
    m = os.environ.get("RAG_MODE", "off").strip().lower()
    if m in ("1", "on", "true", "yes", "vector", "rerank"):
        return "rerank"
    if m in ("bm25", "lexical+bm25"):
        return "bm25"
    return "off"


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
    ชั้นนี้ดูแค่ตัวอักษร ส่วนความหมายเป็นงานของ bm25.py กับ rerank.py
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
    mode = _mode()
    used = "lexical"

    # ขั้นที่หนึ่ง ตัดคำไทยแล้วนับคำซ้ำ
    # แก้จุดอ่อนที่ภาษาไทยไม่เว้นวรรค เช่น สัญญาณไม่ดี กับ คุณภาพสัญญาณ
    if mode in ("bm25", "rerank") and not lexical_solid and bm25.available():
        hits = []
        for f in [x for x in fields if x in RAG_FIELDS]:
            pairs = _values(f)
            counts = dict(pairs)
            for h in bm25.search(f, [v for v, _ in pairs], user_phrase, top_k=VEC_TOP) or []:
                if h["score"] >= bm25.BM25_FLOOR:
                    hits.append((h["score"], f, h["value"], counts.get(h["value"], 0), "bm25"))
        if hits:
            hits.sort(key=lambda t: (-t[0], -t[3]))
            scored = hits
            used = "bm25"

    # ขั้นที่สอง ให้คะแนนทุกค่าในคอลัมน์ด้วย cross-encoder
    # ใช้ต่อเมื่อไม่มีคำไหนซ้ำกันเลย เช่น เน็ตช้า ที่ตัดคำแล้วไม่ชนกับค่าไหน
    if mode == "rerank" and used == "lexical" and not lexical_solid and rerank.available():
        vec = []
        for f in [x for x in fields if x in RAG_FIELDS]:
            pairs = _values(f)
            counts = dict(pairs)
            for h in rerank.score(f, [v for v, _ in pairs], user_phrase, top_k=VEC_TOP) or []:
                if h["score"] >= rerank.RERANK_FLOOR:
                    vec.append((h["score"], f, h["value"], counts.get(h["value"], 0), "rerank"))
        if vec:
            vec.sort(key=lambda t: (-t[0], -t[3]))
            scored = vec
            used = "rerank"

    vector_used = used == "rerank"
    bm25_used = used == "bm25"

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
            "method": used,
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
    cands = [{"value": v, "field": f, "score": round(s, 3),
              "rows_all_years": n, "evidence": k}
             for s, f, v, n, k in same]

    # คำเดียวอาจตรงได้ทั้งประเภทบริการและกลุ่มปัญหา เช่น เน็ตช้า
    # ตรงกับ service_type อินเทอร์เน็ต และ problem_group คุณภาพสัญญาณ พอ ๆ กัน
    # ถ้าเลือกข้างเอง จะได้ตัวเลขที่ถูก syntax แต่ตอบคนละคำถามกับที่ผู้ใช้ถาม
    # คะแนนของ cross-encoder เทียบข้ามคอลัมน์ได้ เพราะมาจากโมเดลเดียวกัน
    cross_field = False
    if vector_used and field is None:
        best = {}
        for sc, f, v, n, k in scored:
            if f not in best or sc > best[f][0]:
                best[f] = (sc, v, n, k)
        rivals = [f for f, (sc, *_) in best.items()
                  if f != top_field and sc >= rerank.RERANK_FLOOR
                  and scored[0][0] - sc < rerank.RERANK_MARGIN]
        if rivals:
            cross_field = True
            picked = [top_field] + rivals
            cands = [{"value": best[f][1], "field": f, "score": round(best[f][0], 3),
                      "rows_all_years": best[f][2], "evidence": best[f][3]}
                     for f in picked][:MAX_CANDIDATES]

    # --- 3. ตัดสิน confidence ---
    if bm25_used:
        # มีคำซ้ำกันจริงจึงเชื่อได้ระดับหนึ่ง แต่ถ้าอันดับหนึ่งกับสองสูสี ให้ถามกลับ
        # ที่วัดมา คู่ที่อันดับหนึ่งผิดมักห่างกันแค่ 1.2 ถึง 1.5 เท่า
        s1 = cands[0]["score"]
        s2 = cands[1]["score"] if len(cands) > 1 else 0.0
        # ไม่มีคู่แข่งเลย แปลว่ามีค่าเดียวที่มีคำซ้ำ เชื่อได้ที่เกณฑ์พื้น
        # มีคู่แข่ง ต้องนำให้ขาดจริง ไม่งั้นถามกลับ
        alone = s2 <= 0 and s1 >= bm25.BM25_FLOOR
        # คะแนน BM25 ของคนละคอลัมน์เทียบกันตรง ๆ ไม่ได้ เพราะคิดจากคลังคำคนละชุด
        # คำที่หายากในคอลัมน์เล็กจะได้คะแนนพุ่งกว่าคำเดียวกันในคอลัมน์ใหญ่เสมอ
        # ถ้าผู้ใช้ไม่ได้ระบุคอลัมน์ จึงให้ได้แค่ ambiguous แล้วถามกลับว่าหมายถึงด้านไหน
        single_field = field is not None
        if single_field and (
            alone or (s1 >= bm25.BM25_HIGH and s2 > 0 and s1 / s2 >= bm25.BM25_RATIO)
        ):
            conf, note = "high", (
                "จับคู่ได้จากคำที่ซ้ำกันหลังตัดคำไทย ใช้ค่านี้ได้ "
                "ต้องบอกผู้ใช้ว่าตีความคำของเขาเป็นค่าอะไรในฐานข้อมูล "
                "ตัวเลข rows คือยอดรวมทุกปี ไม่ใช่คำตอบ ต้องเรียก query_complaints ต่อเสมอ")
        else:
            conf, note = "ambiguous", (
                "มีคำซ้ำกันหลายค่าและคะแนนสูสี ห้ามเดา "
                "ให้ถามผู้ใช้ว่าหมายถึงอันไหน พร้อมบอกจำนวนเรื่องของแต่ละตัวเลือก")
    elif cross_field:
        conf, note = "ambiguous", (
            "คำนี้ตรงได้หลายคอลัมน์พอ ๆ กัน ห้ามเลือกเอง "
            "ให้ถามผู้ใช้ว่าหมายถึงด้านไหน โดยอธิบายความต่างให้ชัด "
            "ดูฟิลด์ field ในแต่ละตัวเลือกว่ามาจากคอลัมน์อะไร "
            "เช่นกรองที่ประเภทบริการจะได้ทุกเรื่องของบริการนั้น "
            "ส่วนกรองที่กลุ่มปัญหาจะได้เฉพาะเรื่องที่เป็นปัญหานั้นจริง ๆ")
    elif vector_used:
        # จับคู่ด้วยความหมาย ไม่มีหลักฐานตัวอักษรรองรับ จึงต้องเข้มกว่าปกติ
        # ถ้าไม่ขาดลอยจริง ให้ถามกลับ ดีกว่าเดาแล้วได้ SQL ที่ถูก syntax แต่ผิดความหมาย
        clear = cands[0]["score"] >= rerank.RERANK_HIGH and (
            len(cands) == 1
            or cands[0]["score"] - cands[1]["score"] >= rerank.RERANK_MARGIN
        )
        if clear:
            conf, note = "high", (
                "ไม่มีคำไหนตรงตัว แต่โมเดลให้คะแนนความเกี่ยวข้องสูงชัดเจน ใช้ค่านี้ได้ "
                "ต้องบอกผู้ใช้ให้ชัดว่าตีความคำของเขาเป็นค่าอะไรในฐานข้อมูล "
                "ตัวเลข rows คือยอดรวมทุกปี ไม่ใช่คำตอบ ต้องเรียก query_complaints ต่อเสมอ")
        else:
            conf, note = "ambiguous", (
                "ให้คะแนนความเกี่ยวข้องแล้วได้หลายค่าที่สูสีกัน ห้ามเดา "
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
        "method": used,
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
