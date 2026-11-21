"""
lawrag/emb_from_db.py - ดึงเวกเตอร์ที่เคย encode ไว้ออกจาก pgvector มาทำเป็นแคชของ index.py

ทำไมต้องมีไฟล์นี้
    ตอน encode ครั้งก่อนใช้เวลาไป 150 นาที ผลลัพธ์ถูกเก็บไว้สองที่
        data/law/emb_*.npy   ไฟล์แคชข้างเครื่อง  ตอนนี้ไม่อยู่บนดิสก์แล้ว
        ตาราง law_vec_plain และ law_vec_ctx ใน pgvector  ยังอยู่ครบ
    และ index.py ขั้นแรกสุดคือ prepare() ที่สั่ง DROP TABLE ทิ้งทั้งหมด
    ถ้ารัน index.py ตรง ๆ เวกเตอร์ชุดเดิมจะหายไปก่อนที่จะมีใครได้ใช้
    แล้วต้อง encode 682 ก้อนเดิมใหม่ทั้งที่ข้อความไม่ได้เปลี่ยนสักตัว

    ไฟล์นี้จึงต้องรัน "ก่อน" index.py เสมอ หน้าที่คือย้ายของเก่าออกมาเก็บไว้ให้ปลอดภัย
    เสร็จแล้ว index.py จะหยิบไปใช้เอง และ encode เฉพาะก้อนที่เพิ่มเข้ามาใหม่

กุญแจของแคชคืออะไร
    SHA-1 ของข้อความที่ถูก encode จริง ๆ ไม่ใช่ chunk id
    เพราะถ้าเราแก้ตัวบทแล้ว id ยังเหมือนเดิม การใช้ id เป็นกุญแจจะหยิบเวกเตอร์เก่ามาใช้ผิด ๆ
    ใช้ SHA-1 ของข้อความแทน พอข้อความเปลี่ยนแม้ตัวเดียว กุญแจก็เปลี่ยน แคชจะไม่ถูกแตะ

ไฟล์ออก : data/law/emb_plain.npy + emb_plain_keys.json
          data/law/emb_ctx.npy   + emb_ctx_keys.json

รัน : docker compose --profile tools run --rm lawrag python -m lawrag.emb_from_db
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
PGDSN = os.environ.get("PGDSN", "postgresql://law:law@localhost:5433/law")
DIM = 1024

# ชื่อชุด -> (ชื่อตารางเวกเตอร์, ชื่อคอลัมน์ข้อความใน law_chunk)
VARIANTS = {"plain": ("law_vec_plain", "text"),
            "ctx": ("law_vec_ctx", "text_ctx")}


# ส่วนที่ 1: แปลงค่าที่ pgvector คืนมาให้เป็นรายการตัวเลข
# pgvector คืนมาเป็นสตริงหน้าตา "[0.013,-0.004,...]" ถ้าไม่ได้ลงทะเบียนชนิดไว้
# รับ: ค่าที่อ่านได้จากคอลัมน์ · คืน: list ของ float
def to_vec(raw) -> list[float]:
    if isinstance(raw, (list, tuple)):
        return list(raw)
    s = str(raw).strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [float(x) for x in s.split(",") if x.strip()]


def main() -> int:
    import numpy as np
    import psycopg

    try:
        conn = psycopg.connect(PGDSN, connect_timeout=15)
    except Exception as e:
        print("ต่อฐานไม่ได้ %s: %s" % (type(e).__name__, str(e)[:120]), file=sys.stderr)
        print("ถ้า pgvec ยังไม่ขึ้น ให้สั่ง docker compose up -d pgvec ก่อน", file=sys.stderr)
        return 2

    with conn.cursor() as cur:
        cur.execute("SELECT to_regclass('law_chunk')")
        if cur.fetchone()[0] is None:
            print("ยังไม่มีตาราง law_chunk ในฐาน ไม่มีอะไรให้ดึง", file=sys.stderr)
            return 1

    total_saved = 0
    for variant, (table, field) in VARIANTS.items():
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s)", (table,))
            if cur.fetchone()[0] is None:
                print("ไม่พบตาราง %s ข้ามชุด %s" % (table, variant))
                continue
            cur.execute(
                "SELECT c.%s, v.emb FROM law_chunk c JOIN %s v ON v.chunk_id = c.id"
                % (field, table))
            rows = cur.fetchall()

        keys, vecs, bad = [], [], 0
        for txt, raw in rows:
            if not txt:
                bad += 1
                continue
            v = to_vec(raw)
            if len(v) != DIM:
                bad += 1
                continue
            keys.append(hashlib.sha1(txt.encode("utf-8")).hexdigest())
            vecs.append(v)

        if not vecs:
            print("ชุด %s ไม่มีเวกเตอร์ที่ใช้ได้" % variant)
            continue

        # ข้อความซ้ำกันสองก้อนจะได้กุญแจเดียวกัน เก็บไว้ตัวเดียวพอ
        seen, k2, v2 = set(), [], []
        for k, v in zip(keys, vecs):
            if k in seen:
                continue
            seen.add(k); k2.append(k); v2.append(v)

        arr = np.asarray(v2, dtype="float32")
        np.save(os.path.join(LAW_DIR, "emb_%s.npy" % variant), arr)
        json.dump(k2, open(os.path.join(LAW_DIR, "emb_%s_keys.json" % variant),
                           "w", encoding="utf-8"))
        total_saved += len(k2)
        print("ชุด %-5s ดึงมาได้ %d แถว เก็บเป็นแคช %d ก้อน ข้ามที่ใช้ไม่ได้ %d"
              % (variant, len(rows), len(k2), bad))

    conn.close()
    print()
    print("เก็บแคชรวม %d ก้อน  ตอนนี้สั่ง index.py ได้ มันจะ encode เฉพาะก้อนใหม่"
          % total_saved)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
