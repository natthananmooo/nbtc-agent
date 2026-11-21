"""
lawrag/index.py - ทำดัชนีสองชุดสำหรับเทียบ Standard Retrieval กับ Contextual Retrieval

สองชุดต่างกันที่ข้อความที่เอาไปทำดัชนีเท่านั้น โค้ดเดียวกันทั้งหมด
    ชุด plain  ใช้ text            คือหัวเรื่องโครงสร้าง + ตัวบท
    ชุด ctx    ใช้ text_contextual คือคำบรรยายที่ LLM เขียน + ข้อความเดียวกันข้างบน
    การแยกให้ต่างกันแค่ฟิลด์เดียว ทำให้ผลที่วัดได้เป็นผลของบริบทล้วน ๆ
    ไม่ปนกับความต่างของโมเดลหรือพารามิเตอร์

แต่ละชุดสร้างสองดัชนี
    dense  เวกเตอร์ 1024 มิติจาก BAAI/bge-m3 เก็บใน Postgres ที่เปิดส่วนขยาย pgvector
    BM25   นับคำที่ตัดด้วย pythainlp newmm เก็บเป็นไฟล์ pickle

ทำไม BM25 ไม่ใช้ sparse vector ของ BGE-M3 แบบในบทความของอาจารย์
    BGE-M3 ให้ทั้ง dense และ sparse จากการ encode ครั้งเดียว ซึ่งสะดวกกว่า
    แต่ตัวตัดคำของมันเป็น subword ที่ฝึกมาหลายภาษารวมกัน กับภาษาไทยจึงตัดไม่ตรงคำ
    ส่วน newmm ของ pythainlp ตัดตามพจนานุกรมไทย และโครงการนี้ใช้อยู่แล้ว
    ในชั้นค้นค่าของฝั่ง SQL จึงใช้ตัวเดียวกัน จะได้ไม่ต้องดูแลสองระบบ

ทำไมต้องเก็บเวกเตอร์ลงไฟล์ก่อน แล้วค่อยเขียนฐานข้อมูล
    รอบแรกรันแล้วพังตอน executemany ด้วย server closed the connection unexpectedly
    คือ Postgres ถูกฆ่าทิ้งระหว่างเขียน ไม่ใช่ SQL ผิด สาเหตุคือแรมของเครื่องตึง
    ตอนนั้นโมเดล bge-m3 ยังค้างอยู่ในแรมราวสองกิกะ พร้อมกับคอนเทนเนอร์ Postgres
    ของเดิมเขียนไว้เป็น encode แล้วเขียนฐานข้อมูลต่อกันในฟังก์ชันเดียว
    พอฐานข้อมูลตาย เวลา encode ยี่สิบสี่นาทีจึงหายไปทั้งหมด ต้องเริ่มใหม่หมด

    จึงแยกเป็นสามขั้นที่ไม่ขึ้นต่อกัน
      ขั้นที่หนึ่ง  encode ทั้งสองชุด เก็บเป็น data/law/emb_<ชุด>.npy ยังไม่แตะฐานข้อมูล
      ขั้นที่สอง   ปล่อยโมเดลออกจากแรม แล้วค่อยต่อฐานข้อมูล
      ขั้นที่สาม   เขียนทีละก้อนเล็กและ commit ทุกก้อน ไม่ส่งทีเดียวทั้งชุด
    รันซ้ำจะเห็นว่ามีไฟล์ .npy อยู่แล้วและข้าม encode ไปเลย เสียเวลาแค่ขั้นเขียน

ไฟล์เข้า : data/law/chunks_contextual.json
ผลลัพธ์  : ตาราง law_chunk และ law_vec_plain law_vec_ctx ใน Postgres
           และ data/law/bm25_plain.pkl กับ data/law/bm25_ctx.pkl
"""
from __future__ import annotations

import json
import os
import pickle
import sys
import time

# ส่วนที่ 1: ค่าตั้งต้น
BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
IN_PATH = os.path.join(LAW_DIR, "chunks_contextual.json")
PGDSN = os.environ.get("PGDSN", "postgresql://law:law@localhost:5433/law")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
DIM = 1024
BATCH = 16          # ขนาดก้อนตอน encode บน CPU ใหญ่กว่านี้กินแรมโดยไม่เร็วขึ้น
INSERT_BATCH = 64   # ขนาดก้อนตอนเขียนฐานข้อมูล เล็กไว้เพื่อให้ commit ได้ถี่

# สองชุดที่จะทำดัชนี ชื่อชุด -> ฟิลด์ที่ใช้เป็นข้อความ
VARIANTS = {"plain": "text", "ctx": "text_contextual"}


# ส่วนที่ 2: ตัดคำไทยสำหรับ BM25
# หน้าที่: ตัดข้อความเป็นคำด้วย newmm แล้วทิ้งช่องว่างกับคำสั้นเกินไป
# รับ: ข้อความ · คืน: รายการคำ
def tokenize(text: str) -> list[str]:
    from pythainlp.tokenize import word_tokenize
    return [w for w in word_tokenize(text, engine="newmm")
            if w.strip() and len(w.strip()) > 1]


# ส่วนที่ 3: เตรียมตารางใน Postgres
# หน้าที่: สร้างส่วนขยาย pgvector และตารางที่ต้องใช้ ล้างของเดิมทิ้งทุกครั้ง
# ทำไมต้องล้าง: ถ้าแก้ chunk แล้วทำดัชนีทับ จะเหลือแถวเก่าที่ไม่มีใน chunks.json แล้ว
#               ซึ่งจะโผล่มาเป็นผลการค้นที่ไม่มีอยู่จริง หาสาเหตุยากมาก
# รับ: connection · คืน: ไม่คืนค่า
def prepare(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("DROP TABLE IF EXISTS law_vec_plain, law_vec_ctx, law_chunk CASCADE")
        cur.execute("""
            CREATE TABLE law_chunk (
                id          text PRIMARY KEY,
                doc_file    text,
                doc_title   text,
                law_type    text,
                category    text,
                url         text,
                unit        text,
                number      int,
                muad        text,
                body        text,
                context     text,
                text        text,
                text_ctx    text
            )""")
        for v in VARIANTS:
            cur.execute(f"""
                CREATE TABLE law_vec_{v} (
                    chunk_id text PRIMARY KEY REFERENCES law_chunk(id) ON DELETE CASCADE,
                    emb      vector({DIM})
                )""")
    conn.commit()


# ส่วนที่ 4: ใส่ข้อมูลตัวบทลงตาราง
# หน้าที่: เขียน chunk ทั้งหมดลง law_chunk · รับ: connection, รายการ chunk · คืน: ไม่คืนค่า
def insert_chunks(conn, rows: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO law_chunk
               (id, doc_file, doc_title, law_type, category, url,
                unit, number, muad, body, context, text, text_ctx)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            [(r["id"], r["doc_file"], r["doc_title"], r["law_type"], r["category"],
              r["url"], r["unit"], r["number"], r["muad"], r["body"],
              r.get("context", ""), r["text"], r.get("text_contextual", r["text"]))
             for r in rows])
    conn.commit()


# ส่วนที่ 5: ขั้นที่หนึ่ง encode แล้วเก็บลงไฟล์
# หน้าที่: แปลงข้อความทั้งชุดเป็นเวกเตอร์ ถ้าเคยทำไว้แล้วให้ใช้ของเดิม
# ทำไมเก็บลงไฟล์: ขั้นนี้นานที่สุด ถ้าขั้นเขียนฐานข้อมูลพัง จะได้ไม่ต้อง encode ใหม่
#
# ทำไมต้องเก็บเป็นรายก้อน ไม่ใช่ทั้งอาเรย์
#     เดิมเทียบแค่ขนาดอาเรย์ว่าเท่ากับจำนวน chunk ไหม พอเพิ่มกฎหมายเข้ามาหนึ่งฉบับ
#     ขนาดเปลี่ยน แคชใช้ไม่ได้ทั้งก้อน ต้อง encode ใหม่ทั้ง 682 ก้อน กินเวลาเป็นชั่วโมง
#     ทั้งที่ของเดิม 682 ก้อนนั้นข้อความไม่ได้เปลี่ยนเลย
#     จึงเปลี่ยนมาเก็บกุญแจเป็น SHA-1 ของข้อความที่ encode จริง ๆ เรียงคู่กับอาเรย์
#     ก้อนไหนข้อความเดิมก็หยิบเวกเตอร์เดิมมาใช้ ก้อนใหม่หรือก้อนที่ข้อความเปลี่ยนจึง encode
#     ปลอดภัยเพราะ BGE-M3 ให้ผลคงที่ ข้อความเดียวกันได้เวกเตอร์เดียวกันเสมอ
#     และถ้าข้อความเปลี่ยนแม้ตัวอักษรเดียว SHA-1 ก็เปลี่ยน แคชจะไม่ถูกใช้
#
# รับ: โมเดล, รายการ chunk, ชื่อชุด, ชื่อฟิลด์ · คืน: (เวกเตอร์, วินาทีที่ใช้ encode)
def encode_variant(model, rows: list[dict], variant: str, field: str):
    import hashlib

    import numpy as np

    path = os.path.join(LAW_DIR, f"emb_{variant}.npy")
    keys_path = os.path.join(LAW_DIR, f"emb_{variant}_keys.json")
    want = [hashlib.sha1(r[field].encode("utf-8")).hexdigest() for r in rows]

    cache: dict[str, "np.ndarray"] = {}
    if os.path.exists(path) and os.path.exists(keys_path):
        old_vecs = np.load(path)
        old_keys = json.load(open(keys_path, encoding="utf-8"))
        if len(old_keys) == old_vecs.shape[0] and old_vecs.shape[1] == DIM:
            cache = {k: old_vecs[i] for i, k in enumerate(old_keys)}
        else:
            print("   แคช emb_%s เสียรูป จะ encode ใหม่ทั้งหมด" % variant, flush=True)
    elif os.path.exists(path):
        # แคชรุ่นเก่าที่ยังไม่มีไฟล์กุญแจ ใช้ได้ต่อเมื่อจำนวนก้อนยังเท่าเดิมเป๊ะ
        old_vecs = np.load(path)
        if old_vecs.shape == (len(rows), DIM):
            print("   แคช emb_%s รุ่นเก่า จำนวนก้อนตรงกัน ใช้ต่อและเขียนกุญแจกำกับ"
                  % variant, flush=True)
            np.save(path, old_vecs)
            json.dump(want, open(keys_path, "w", encoding="utf-8"))
            return old_vecs, 0.0

    todo = [i for i, k in enumerate(want) if k not in cache]
    print("   emb_%s ใช้ของเดิม %d ก้อน ต้อง encode ใหม่ %d ก้อน"
          % (variant, len(rows) - len(todo), len(todo)), flush=True)

    t0 = time.time()
    if todo:
        fresh = model.encode([rows[i][field] for i in todo], batch_size=BATCH,
                             normalize_embeddings=True, show_progress_bar=False)
        fresh = np.asarray(fresh, dtype="float32")
        for n, i in enumerate(todo):
            cache[want[i]] = fresh[n]
    sec = time.time() - t0

    vecs = np.vstack([cache[k] for k in want]).astype("float32")
    np.save(path, vecs)
    json.dump(want, open(keys_path, "w", encoding="utf-8"))
    return vecs, sec


# ส่วนที่ 5.1: ขั้นที่สาม เขียนเวกเตอร์ลง pgvector
# หน้าที่: เขียนทีละก้อนเล็กและ commit ทุกก้อน แล้วจึงสร้างดัชนี HNSW
# ทำไมไม่ส่งทีเดียว: executemany ก้อนเดียวทั้งชุดทำให้ฝั่งเซิร์ฟเวอร์ค้างงานไว้มาก
#                    ถ้าโดนฆ่ากลางทาง จะไม่รู้เลยว่าค้างที่แถวไหน
# รับ: connection, รายการ chunk, เวกเตอร์, ชื่อชุด · คืน: เวลาที่ใช้เป็นวินาที
def insert_dense(conn, rows: list[dict], vecs, variant: str) -> float:
    t0 = time.time()
    done = 0
    for i in range(0, len(rows), INSERT_BATCH):
        part = [(r["id"], "[" + ",".join("%.6f" % x for x in v) + "]")
                for r, v in zip(rows[i:i + INSERT_BATCH], vecs[i:i + INSERT_BATCH])]
        with conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO law_vec_{variant} (chunk_id, emb) VALUES (%s, %s)", part)
        conn.commit()
        done += len(part)
        print("      เขียนแล้ว %d/%d แถว" % (done, len(rows)), end="\r", flush=True)
    with conn.cursor() as cur:
        # ใช้ cosine เพราะเวกเตอร์ถูก normalize แล้ว ระยะ cosine จึงเทียบได้ตรง
        cur.execute(f"""CREATE INDEX ON law_vec_{variant}
                        USING hnsw (emb vector_cosine_ops)""")
    conn.commit()
    print("      เขียนครบ %d แถว และสร้างดัชนี HNSW แล้ว" % done, flush=True)
    return time.time() - t0


# ส่วนที่ 6: สร้างดัชนี BM25 แล้วเก็บเป็นไฟล์
# หน้าที่: ตัดคำทั้งชุดแล้วสร้าง BM25Okapi เก็บลง pickle พร้อมลำดับ id
# รับ: รายการ chunk, ชื่อชุด, ชื่อฟิลด์ · คืน: เวลาที่ใช้เป็นวินาที
def build_bm25(rows: list[dict], variant: str, field: str) -> float:
    from rank_bm25 import BM25Okapi
    t0 = time.time()
    corpus = [tokenize(r[field]) for r in rows]
    bm25 = BM25Okapi(corpus)
    out = os.path.join(LAW_DIR, f"bm25_{variant}.pkl")
    # เก็บลำดับ id ไว้ด้วย เพราะ BM25Okapi คืนคะแนนเป็นลำดับเดียวกับ corpus เท่านั้น
    with open(out, "wb") as f:
        pickle.dump({"bm25": bm25, "ids": [r["id"] for r in rows],
                     "variant": variant, "field": field}, f)
    return time.time() - t0


# ส่วนที่ 7: จุดเริ่มทำงาน
def main() -> int:
    import gc
    import psycopg
    from sentence_transformers import SentenceTransformer

    rows = json.load(open(IN_PATH, encoding="utf-8"))
    missing = [r["id"] for r in rows if not r.get("text_contextual")]
    if missing:
        print("มี %d ก้อนที่ยังไม่มีบริบท ให้รัน lawrag/contextualize.py ก่อน"
              % len(missing), file=sys.stderr)
        return 1

    # ขั้นที่หนึ่ง encode ทั้งสองชุดให้เสร็จก่อน ยังไม่ต่อฐานข้อมูล
    print("โหลดโมเดล %s" % EMBED_MODEL, flush=True)
    model = SentenceTransformer(EMBED_MODEL)
    vecs = {}
    for variant, field in VARIANTS.items():
        avg = sum(len(r[field]) for r in rows) / len(rows)
        print("ชุด %-6s ใช้ฟิลด์ %-16s ความยาวเฉลี่ย %.0f ตัวอักษร"
              % (variant, field, avg), flush=True)
        vecs[variant], sec = encode_variant(model, rows, variant, field)
        if sec:
            print("   encode %.0f วินาที เก็บไว้ที่ emb_%s.npy" % (sec, variant),
                  flush=True)

    # ขั้นที่สอง คืนแรมของโมเดลก่อน แล้วค่อยยุ่งกับฐานข้อมูล
    # ลำดับนี้สำคัญ เพราะถ้าถือโมเดลไว้ตอนเขียน เครื่องที่แรมตึงจะฆ่า Postgres ทิ้ง
    del model
    gc.collect()
    print("\nคืนแรมของโมเดลแล้ว เริ่มเขียนฐานข้อมูล", flush=True)

    print("ต่อฐานข้อมูล %s" % PGDSN.split("@")[-1], flush=True)
    conn = psycopg.connect(PGDSN)
    prepare(conn)
    insert_chunks(conn, rows)
    print("ใส่ตัวบทแล้ว %d ก้อน\n" % len(rows), flush=True)

    # ขั้นที่สาม เขียนเวกเตอร์และทำ BM25 ทีละชุด
    for variant, field in VARIANTS.items():
        print("ชุด %s" % variant, flush=True)
        d = insert_dense(conn, rows, vecs[variant], variant)
        b = build_bm25(rows, variant, field)
        print("   เขียน dense %.0f วินาที · BM25 %.0f วินาที\n" % (d, b), flush=True)

    with conn.cursor() as cur:
        for v in VARIANTS:
            cur.execute(f"SELECT count(*) FROM law_vec_{v}")
            print("law_vec_%-6s %d แถว" % (v, cur.fetchone()[0]))
    conn.close()
    print("\nทำดัชนีครบทั้งสองชุดแล้ว")
    return 0


if __name__ == "__main__":
    sys.exit(main())
