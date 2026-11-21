"""
lawrag/contextualize.py - ให้ LLM เขียนคำบรรยายบริบทกำกับทุก chunk ก่อนนำไปทำดัชนี

นี่คือหัวใจของ Contextual Retrieval ตามที่ Anthropic เสนอ
    chunk ที่ตัดออกมาจากเอกสารยาวจะเสียบริบทไป เช่น ข้อความว่า
    "ให้แจ้งผลภายในสามสิบวัน" เพียว ๆ ไม่บอกว่าเป็นสามสิบวันของเรื่องอะไร ของใคร
    วิธีแก้คือให้โมเดลอ่านทั้งฉบับแล้วเขียนสองสามประโยคว่า chunk นี้คือส่วนไหนของฉบับ
    แล้วเอาคำบรรยายนั้นต่อหน้า chunk ก่อน embed และก่อนทำดัชนี BM25

ต่างจากบริบทคงที่ที่ chunker ใส่ไว้แล้วอย่างไร
    chunker ใส่ [ชื่อฉบับ] [หมวด] ซึ่งเป็นข้อเท็จจริงเชิงโครงสร้าง ได้มาฟรีจากตัวบท
    ส่วนขั้นนี้ให้โมเดลเขียนว่า "ข้อนี้กำหนดกรอบเวลาที่เลขาธิการต้องแจ้งผล
    แก่ผู้ร้องเรียน ต่อเนื่องจากข้อ ๑๖ ที่ว่าด้วยการมอบอำนาจ" ซึ่งเป็นความเข้าใจ
    ที่อ่านจากทั้งฉบับแล้วสรุป ไม่มีทางได้จากการตัดคำ

ที่ปรับจากต้นฉบับของ Anthropic
    ต้นฉบับส่งเนื้อเอกสารทั้งฉบับเข้าไปทุกครั้งแล้วพึ่ง prompt caching ให้ถูก
    ฉบับที่ยาวที่สุดของเราคือ พ.ร.บ. องค์กรจัดสรรคลื่นความถี่ 265,976 ตัวอักษร
    ส่งทั้งฉบับ 363 ครั้งไม่คุ้ม จึงส่งสารบัญของฉบับแทนเมื่อเอกสารยาวเกินเกณฑ์
    สารบัญคือชื่อฉบับ อารัมภบท และหัวข้อทุกข้อพร้อมใจความสั้น ๆ
    ฉบับที่สั้นกว่าเกณฑ์ยังส่งเนื้อเต็มเหมือนต้นฉบับ

ไฟล์เข้า : data/law/chunks.json
ไฟล์ออก : data/law/chunks_contextual.json  เขียนทับได้เรื่อย ๆ และรันต่อจากของเดิมได้
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed



# อ่านคีย์จาก .env เหมือนที่ agent.py ทำ สคริปต์นี้รันบนเครื่อง ไม่ได้รันในคอนเทนเนอร์
# คอนเทนเนอร์ mcp ไม่มีคีย์ และ web มีคีย์แต่ไม่มีโฟลเดอร์ data/law ให้เขียนผล
#
# ใช้ python-dotenv ถ้ามี ถ้าไม่มีก็อ่านไฟล์เองแบบง่าย ๆ
# เพราะสคริปต์นี้อาจถูกรันด้วย python ตัวที่ยังไม่ได้ลงแพ็กเกจของโปรเจกต์
def load_env(path: str = ".env") -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv()
        return
    except ImportError:
        pass
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", path)
    for cand in (path, here):
        if not os.path.exists(cand):
            continue
        for line in open(cand, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
        return


load_env()

# ส่วนที่ 1: ค่าตั้งต้น
BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
IN_PATH = os.path.join(LAW_DIR, "chunks.json")
OUT_PATH = os.path.join(LAW_DIR, "chunks_contextual.json")

# ใช้โมเดลถูกตัวเล็กได้ เพราะงานนี้คือสรุปบริบท ไม่ใช่ตอบคำถามกฎหมาย
MODEL = os.environ.get("CONTEXT_MODEL", "openai/gpt-4.1-mini")
WORKERS = int(os.environ.get("CONTEXT_WORKERS", "8"))
FULL_DOC_LIMIT = 12000   # ฉบับที่สั้นกว่านี้ส่งเนื้อเต็ม ยาวกว่านี้ส่งสารบัญแบบหน้าต่าง
WINDOW = 12              # ฉบับยาว ส่งสารบัญเฉพาะข้อรอบ ๆ ข้อที่กำลังทำ กี่ข้อต่อข้าง
VIEW_CAP = 5000          # เพดานความยาวของสารบัญที่ส่งไป กันไม่ให้ค่าเรียกโมเดลบานปลาย
RETRIES = 3

SYSTEM = (
    "คุณเป็นผู้ช่วยจัดทำดัชนีค้นคืนเอกสารกฎหมายไทย "
    "หน้าที่คือเขียนคำบรรยายบริบทสั้น ๆ กำกับข้อความที่ถูกตัดออกมาจากเอกสาร "
    "เพื่อให้ข้อความนั้นค้นเจอได้ง่ายขึ้นเมื่ออยู่ลำพัง"
)

USER_TMPL = """นี่คือเอกสารกฎหมายทั้งฉบับหรือสารบัญของฉบับ
<document>
{doc}
</document>

นี่คือข้อความส่วนหนึ่งที่ถูกตัดออกมาจากเอกสารข้างบน
<chunk>
{chunk}
</chunk>

เขียนคำบรรยายบริบทของข้อความส่วนนี้ ความยาวไม่เกิน 2 ประโยค และไม่เกิน 220 ตัวอักษร
ให้มีสามอย่างนี้ครบ
  ส่วนนี้อยู่ตรงไหนของฉบับ และฉบับนี้ว่าด้วยเรื่องอะไร
  ส่วนนี้กำหนดเรื่องอะไร ใครมีหน้าที่
  คำที่คนทั่วไปน่าจะใช้ค้นหาเรื่องนี้ ซึ่งมักไม่ตรงกับถ้อยคำในตัวบท

กติกาการตอบ
  ขึ้นต้นด้วยชื่อเรื่องที่ส่วนนี้ว่าด้วย เช่น กรอบเวลาแจ้งผลการพิจารณาเรื่องร้องเรียน
  ห้ามขึ้นต้นด้วยคำว่า ข้อความนี้ ส่วนนี้ chunk นี้ เอกสารนี้ หรือคำทำนองเดียวกัน
  ห้ามลอกตัวบทมาซ้ำ ให้สรุปด้วยคำของตัวเอง
  ตอบเฉพาะคำบรรยาย ไม่ต้องมีหัวข้อหรือเครื่องหมายคำพูด"""


# ส่วนที่ 2: ประกอบ "เอกสาร" ที่จะส่งให้โมเดลอ่าน
# ทำไมไม่ส่งทั้งฉบับทุกครั้งแบบต้นฉบับ
#     วัดแล้วถ้าส่งสารบัญเต็มของทุกฉบับ จะต้องส่งราว 22.7 ล้าน token
#     เพราะ พ.ร.บ. องค์กรจัดสรรคลื่นความถี่มี 363 ก้อน สารบัญเต็มยาวมาก
#     แล้วถูกส่งซ้ำ 363 ครั้ง จึงส่งเฉพาะสารบัญรอบ ๆ ข้อที่กำลังทำแทน
#     ซึ่งเป็นบริบทที่ใกล้เคียงและมีประโยชน์กว่าสารบัญของทั้งฉบับอยู่แล้ว
# หน้าที่: คืนเนื้อเต็มถ้าฉบับสั้น หรือสารบัญแบบหน้าต่างถ้าฉบับยาว
# รับ: รายการ chunk ของฉบับเดียวกัน และตำแหน่งของก้อนที่กำลังทำ
# คืน: ข้อความแทนเอกสาร ยาวไม่เกิน VIEW_CAP
def build_doc_view(doc_chunks: list[dict], focus: int = -1) -> str:
    title = doc_chunks[0]["doc_title"]
    total = sum(c["n_chars"] for c in doc_chunks)
    if total <= FULL_DOC_LIMIT:
        return f"{title}\n\n" + "\n\n".join(c["body"] for c in doc_chunks)

    lines = [title, ""]
    pre = next((c for c in doc_chunks if c["unit"] == "อารัมภบท"), None)
    if pre:
        lines.append("อารัมภบท: " + pre["body"][:600])   # ที่อยู่ของฐานอำนาจ ส่งเสมอ

    lo = max(0, focus - WINDOW) if focus >= 0 else 0
    hi = min(len(doc_chunks), focus + WINDOW + 1) if focus >= 0 else len(doc_chunks)
    if lo > 0:
        lines.append("... (ข้อก่อนหน้าละไว้)")
    for c in doc_chunks[lo:hi]:
        if c["unit"] == "อารัมภบท":
            continue
        head = f"{c['unit']} {c['number']}"
        if c["muad"]:
            head = f"{c['muad']} {head}"
        lines.append(f"{head}: {c['body'][:110]}")
    if hi < len(doc_chunks):
        lines.append("... (ข้อถัดไปละไว้)")
    return "\n".join(lines)[:VIEW_CAP]


# ส่วนที่ 3: เรียกโมเดลหนึ่งครั้งพร้อมลองใหม่เมื่อพลาด
# หน้าที่: ขอคำบรรยายบริบทของ chunk หนึ่งก้อน
# รับ: client, ข้อความแทนเอกสาร, เนื้อ chunk · คืน: คำบรรยาย หรือสตริงว่างถ้าล้มเหลว
def ask_context(client, doc_view: str, chunk_text: str) -> str:
    msg = [{"role": "system", "content": SYSTEM},
           {"role": "user", "content": USER_TMPL.format(doc=doc_view, chunk=chunk_text)}]
    for attempt in range(RETRIES):
        try:
            r = client.chat.completions.create(model=MODEL, messages=msg,
                                               temperature=0, max_tokens=180)
            if getattr(r, "choices", None):
                return (r.choices[0].message.content or "").strip()
        except Exception as e:                      # rate limit หรือ provider ล่มชั่วคราว
            if attempt == RETRIES - 1:
                print("  [ล้มเหลว] %s" % str(e)[:120], file=sys.stderr)
        time.sleep(2 ** attempt)
    return ""


# ส่วนที่ 4: จุดเริ่มทำงาน
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="ทำแค่กี่ก้อน ใช้ลองดูคุณภาพก่อนรันจริง")
    ap.add_argument("--redo", action="store_true",
                    help="ทำใหม่ทั้งหมด ไม่ข้ามก้อนที่มีบริบทแล้ว")
    args = ap.parse_args()

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("ยังไม่ได้ตั้ง OPENROUTER_API_KEY ใน .env", file=sys.stderr)
        return 1
    from openai import OpenAI
    client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")

    rows = json.load(open(IN_PATH, encoding="utf-8"))
    # รันต่อจากของเดิมได้ ถ้าเคยทำไว้แล้วก็ไม่เรียกซ้ำ ประหยัดทั้งเงินและเวลา
    # กุญแจของการ "เคยทำแล้ว" คือ id คู่กับลายนิ้วมือของข้อความ ไม่ใช่ id เดี่ยว ๆ
    # เพราะวันที่ 2026-11-21 เราซ่อมอักขระ PUA ใน 01_act กับ 15_keyword ไป 109 ก้อน
    # id เท่าเดิมแต่ตัวบทเปลี่ยน บริบทที่เขียนไว้ตอนตัวบทยังเป็นขยะจึงไม่ควรเอามาใช้ต่อ
    done: dict[str, str] = {}
    if os.path.exists(OUT_PATH) and not args.redo:
        cur_sha = {r["id"]: hashlib.sha1(r["text"].encode("utf-8")).hexdigest()
                   for r in rows}
        stale = 0
        for r in json.load(open(OUT_PATH, encoding="utf-8")):
            if not r.get("context"):
                continue
            sha = r.get("text_sha")
            if sha is not None and cur_sha.get(r["id"]) != sha:
                stale += 1
                continue
            done[r["id"]] = r["context"]
        if stale:
            print("ตัวบทเปลี่ยนไป %d ก้อน จะเขียนบริบทใหม่ให้" % stale)

    by_doc: dict[str, list[dict]] = {}
    for r in rows:
        by_doc.setdefault(r["doc_file"], []).append(r)
    pos = {r["id"]: i for cs in by_doc.values() for i, r in enumerate(cs)}

    todo = [r for r in rows if r["id"] not in done]
    if args.limit:
        todo = todo[:args.limit]
    print("ทั้งหมด %d ก้อน · มีบริบทแล้ว %d · จะเรียกโมเดล %d ครั้ง · โมเดล %s"
          % (len(rows), len(done), len(todo), MODEL))
    if not todo:
        print("ไม่มีอะไรต้องทำ")
        return 0

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(ask_context, client,
                          build_doc_view(by_doc[r["doc_file"]], pos[r["id"]]),
                          r["text"]): r
                for r in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            r = futs[fut]
            done[r["id"]] = fut.result()
            if i % 25 == 0 or i == len(todo):
                print("  ทำแล้ว %d/%d  ใช้เวลา %.0f วินาที"
                      % (i, len(todo), time.time() - t0), flush=True)

    # ประกอบข้อความที่จะเอาไปทำดัชนี บริบทอยู่หน้า chunk ตามวิธีของ Anthropic
    out = []
    for r in rows:
        ctx = done.get(r["id"], "")
        r2 = dict(r)
        r2["context"] = ctx
        r2["text_sha"] = hashlib.sha1(r["text"].encode("utf-8")).hexdigest()
        r2["text_contextual"] = (ctx + "\n\n" + r["text"]) if ctx else r["text"]
        out.append(r2)
    json.dump(out, open(OUT_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    have = sum(1 for r in out if r["context"])
    print("\nเขียน %s" % OUT_PATH)
    print("มีบริบทแล้ว %d จาก %d ก้อน · ใช้เวลารวม %.0f วินาที"
          % (have, len(out), time.time() - t0))
    if have:
        avg = sum(len(r["context"]) for r in out if r["context"]) / have
        print("ความยาวบริบทเฉลี่ย %.0f ตัวอักษร" % avg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
