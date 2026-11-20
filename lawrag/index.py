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


# ส่วนที่ 5: สร้างเวกเตอร์และใส่ลง pgvector
# หน้าที่: encode ข้อความทั้งชุดแล้วเขียนลงตารางของชุดนั้น
# รับ: connection, โมเดล, รายการ chunk, ชื่อชุด, ชื่อฟิลด์ · คืน: เวลาที่ใช้เป็นวินาที
def build_dense(conn, model, rows: list[dict], variant: str, field: str) -> float:
    t0 = time.time()
    texts = [r[field] for r in rows]
    vecs = model.encode(texts, batch_size=BATCH, normalize_embeddings=True,
                        show_progress_bar=False)
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO law_vec_{variant} (chunk_id, emb) VALUES (%s, %s)",
            [(r["id"], "[" + ",".join("%.6f" % x for x in v) + "]")
             for r, v in zip(rows, vecs)])
        # ใช้ cosine เพราะเวกเตอร์ถูก normalize แล้ว ระยะ cosine จึงเทียบได้ตรง
        cur.execute(f"""CREATE INDEX ON law_vec_{variant}
                        USING hnsw (emb vector_cosine_ops)""")
    conn.commit()
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
    import psycopg
    from sentence_transformers import SentenceTransformer

    rows = json.load(open(IN_PATH, encoding="utf-8"))
    missing = [r["id"] for r in rows if not r.get("text_contextual")]
    if missing:
        print("มี %d ก้อนที่ยังไม่มีบริบท ให้รัน lawrag/contextualize.py ก่อน"
              % len(missing), file=sys.stderr)
        return 1

    print("โหลดโมเดล %s" % EMBED_MODEL, flush=True)
    model = SentenceTransformer(EMBED_MODEL)
    print("ต่อฐานข้อมูล %s" % PGDSN.split("@")[-1], flush=True)
    conn = psycopg.connect(PGDSN)

    prepare(conn)
    insert_chunks(conn, rows)
    print("ใส่ตัวบทแล้ว %d ก้อน\n" % len(rows), flush=True)

    for variant, field in VARIANTS.items():
        avg = sum(len(r[field]) for r in rows) / len(rows)
        print("ชุด %-6s ใช้ฟิลด์ %-16s ความยาวเฉลี่ย %.0f ตัวอักษร"
              % (variant, field, avg), flush=True)
        d = build_dense(conn, model, rows, variant, field)
        b = build_bm25(rows, variant, field)
        print("   dense %.0f วินาที · BM25 %.0f วินาที\n" % (d, b), flush=True)

    with conn.cursor() as cur:
        for v in VARIANTS:
            cur.execute(f"SELECT count(*) FROM law_vec_{v}")
            print("law_vec_%-6s %d แถว" % (v, cur.fetchone()[0]))
    conn.close()
    print("\nทำดัชนีครบทั้งสองชุดแล้ว")
    return 0


if __name__ == "__main__":
    sys.exit(main())
