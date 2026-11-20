"""
lawrag/chunker.py - ตัดกฎหมาย กสทช. เป็น chunk ตามโครงสร้างของตัวบท ไม่ใช่ตามจำนวนตัวอักษร

ทำไมไม่ใช้หน้าต่างเลื่อน 800 ตัวอักษร
    หน่วยที่คนอ้างอิงกฎหมายคือ ข้อ สำหรับประกาศ และ มาตรา สำหรับ พ.ร.บ.
    ถ้าตัดกลางข้อ วรรคท้ายของข้อ ๕ จะไปอยู่ก้อนเดียวกับหัวของข้อ ๖
    แล้วโมเดลจะอ้างเลขข้อผิดด้วยความมั่นใจเต็มร้อย
    แบ่งตามโครงสร้างจึงได้ chunk ที่อ้างอิงได้จริง

ทำไมต้องเติมหัวเรื่องกลับเข้าไปในทุก chunk
    chunk ที่ตัดออกมาจะไม่รู้ว่าตัวเองมาจากประกาศฉบับไหน หมวดอะไร
    คำว่า "ข้อ ๕" เพียว ๆ ไม่มีความหมายถ้าไม่รู้ว่าเป็นข้อ ๕ ของฉบับใด
    จึงเติม [ชื่อฉบับ] [หมวด] ไว้หน้า body ก่อนเอาไปทำดัชนี
    นี่คือบริบทแบบคงที่ ส่วนบริบทที่ LLM เขียนให้จะเพิ่มในขั้นถัดไป

ไฟล์เข้า  : data/law/txt/*.txt ที่แกะจาก PDF แล้ว และ data/law/manifest.csv
ไฟล์ออก  : data/law/chunks.json
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys

# ส่วนที่ 1: ค่าคงที่และตัวช่วยเรื่องเลขไทย
BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))

THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙"  # กฎหมายไทยพิมพ์เลขไทย แต่ผู้ใช้พิมพ์เลขอารบิก


# หน้าที่: แปลงเลขไทยในข้อความเป็นเลขอารบิก เพื่อให้ค้นด้วยเลขทั้งสองแบบได้
# รับ: ข้อความ · คืน: ข้อความที่เลขไทยถูกแทนด้วยเลขอารบิก
def thai_to_arabic(s: str) -> str:
    return "".join(str(THAI_DIGITS.index(c)) if c in THAI_DIGITS else c for c in s)


# หน้าที่: อ่านเลขไทยหรือเลขอารบิกเป็นจำนวนเต็ม
# รับ: ข้อความตัวเลข · คืน: int หรือ None ถ้าแปลงไม่ได้
def to_int(s: str):
    s = thai_to_arabic(s).strip()
    return int(s) if s.isdigit() else None


# ส่วนที่ 2: รูปแบบที่ใช้จับโครงสร้างของตัวบท
# ขยะประจำหน้าของราชกิจจานุเบกษา ต้องลบก่อน ไม่งั้นมันแทรกกลางประโยคทุก ๆ หน้า
RE_PAGE_NUM = re.compile(r"^\s*หน้า\s+[๐-๙\d]+\s*$")
RE_PAGE_HDR = re.compile(r"^\s*เล่ม\s+[๐-๙\d]+.*ราชกิจจานุเบกษา.*$")
RE_PAGE_HDR2 = re.compile(r"^\s*เล่ม\s+[๐-๙\d]+\s+ตอน")

RE_MUAD = re.compile(r"^\s*หมวด\s+([๐-๙\d]+)\s*$")  # หมวด ๑
RE_SUAN = re.compile(r"^\s*ส่วนที่\s+([๐-๙\d]+)\s*$")  # ส่วนที่ ๒
RE_KHOR = re.compile(r"^\s*ข้อ\s+([๐-๙\d]+)\s")  # ข้อ ๕ ใช้กับประกาศและระเบียบ
RE_MATRA_HEAD = re.compile(r"^\s*มาตรา\s+([๐-๙\d]+)\s")  # มาตรา ๕ ใช้กับ พ.ร.บ.

# ภาคผนวกคือเอกสารแนบท้าย ไม่ใช่เนื้อของข้อสุดท้าย
# ถ้าไม่แยก ข้อสุดท้ายจะกวาดตารางตัวชี้วัดทั้งหมดเข้ามา วัดจริงได้ 19 ตอนติดกัน
RE_ANNEX_HEAD = re.compile(r"^\s*(ภาคผนวก|ตารางที่\s)")
# หัวกระดาษที่พิมพ์ซ้ำทุกหน้าในเขตภาคผนวก ไม่ใช่เนื้อหา
RE_ANNEX_RUNHEAD = re.compile(r"^\s*ภาคผนวก\s*แนบท้ายประกาศ")

RE_CITE_MATRA = re.compile(r"มาตรา\s+([๐-๙\d]+)")  # การอ้างถึงมาตราในเนื้อความ
RE_CITE_KHOR = re.compile(r"ข้อ\s+([๐-๙\d]+)")  # การอ้างถึงข้ออื่นในฉบับเดียวกัน
RE_CITE_ACT = re.compile(r"พระราชบัญญัติ[^\s,]{0,60}?\s*พ\.ศ\.\s*([๐-๙\d]+)")
RE_CITE_NOTI = re.compile(r"ประกาศคณะกรรมการกิจการ[^\n]{0,120}?เรื่อง\s+([^\n]{5,90})")

MIN_CHARS = 40     # chunk ที่สั้นกว่านี้เป็นเศษ เช่น หัวข้อลอย ไม่เอาไปทำดัชนี
MAX_CHARS = 2500   # ข้อสุดท้ายของประกาศมักกวาดภาคผนวกและตารางเข้ามาด้วย ต้องซอยต่อ
NIKHAHIT_AA = "\u0e4d\u0e32"   # ◌ํ + า ที่ต้องรวมเป็น ◌ำ ถ้าไฟล์ยังไม่ได้รวมมา


# ส่วนที่ 3: ทำความสะอาดข้อความที่แกะจาก PDF
# หน้าที่: ลบหัวกระดาษของราชกิจจานุเบกษา และยุบบรรทัดว่างซ้อน
# รับ: ข้อความดิบจาก pdftotext · คืน: รายการบรรทัดที่สะอาดแล้ว
def clean_lines(raw: str) -> list[str]:
    out = []
    for line in raw.split("\n"):
        s = line.rstrip()
        if not s.strip():
            continue
        if RE_PAGE_NUM.match(s) or RE_PAGE_HDR.match(s) or RE_PAGE_HDR2.match(s):
            continue  # ขยะประจำหน้า ตัดทิ้ง
        out.append(s.strip())
    return out


# ส่วนที่ 4: ตัดเป็น chunk ทีละฉบับ
# หน้าที่: เดินทีละบรรทัด จำหัวข้อระดับหมวดไว้ แล้วปิดก้อนทุกครั้งที่เจอ ข้อ หรือ มาตรา ใหม่
# รับ: ข้อความทั้งฉบับ, metadata ของฉบับ, ชนิดหน่วย (ข้อ หรือ มาตรา)
# คืน: รายการ dict ของ chunk
def split_document(raw: str, meta: dict, unit: str) -> list[dict]:
    head_re = RE_MATRA_HEAD if unit == "มาตรา" else RE_KHOR
    lines = clean_lines(raw)

    # แยกเขตภาคผนวกออกก่อน แล้วค่อยตัดตัวบท
    # ภาคผนวกเริ่มที่บรรทัด ภาคผนวก หรือ ตารางที่ บรรทัดแรกที่อยู่หลังหัวข้อสุดท้ายของตัวบท
    # ต้องดูตำแหน่งแบบนี้ เพราะคำว่าภาคผนวกถูกพิมพ์ซ้ำเป็นหัวกระดาษของทุกหน้าในเขตนั้น
    # ถ้าเจอที่ไหนแล้วสลับโหมดทันที ข้อ ๕ ที่อยู่ถัดจากการอ้างถึงภาคผนวกจะถูกนับผิด
    heads = [i for i, l in enumerate(lines) if head_re.match(l)]
    last_head = heads[-1] if heads else -1
    annex_at = next((i for i, l in enumerate(lines)
                     if i > last_head and RE_ANNEX_HEAD.match(l)), len(lines))
    main_lines, annex_lines = lines[:annex_at], lines[annex_at:]

    chunks: list[dict] = []
    cur_muad = ""      # หมวดปัจจุบัน จำไว้เพื่อเติมกลับเข้า chunk
    cur_suan = ""      # ส่วนที่ปัจจุบัน
    cur_no = None      # เลขข้อหรือเลขมาตราที่กำลังสะสมอยู่
    body: list[str] = []
    preamble: list[str] = []   # ความก่อนถึงข้อแรก คือเจตนารมณ์และฐานอำนาจ

    # หน้าที่: ปิดก้อนที่สะสมไว้แล้วใส่ลงรายการ ซอยก่อนถ้ายาวเกินเพดาน
    def flush():
        nonlocal cur_no, body
        if cur_no is None or not body:
            cur_no, body = None, []
            return
        text = " ".join(body).strip()
        if len(text) >= MIN_CHARS:
            pieces = split_oversize(text)
            for i, piece in enumerate(pieces):
                part = (i + 1, len(pieces)) if len(pieces) > 1 else None
                chunks.append(make_chunk(meta, unit, cur_no, cur_muad, cur_suan, piece, part))
        cur_no, body = None, []

    for line in main_lines:
        m_mu = RE_MUAD.match(line)
        m_su = RE_SUAN.match(line)
        m_hd = head_re.match(line)
        if m_mu:
            flush()
            cur_muad, cur_suan = "หมวด " + thai_to_arabic(m_mu.group(1)), ""
            continue
        if m_su:
            flush()
            cur_suan = "ส่วนที่ " + thai_to_arabic(m_su.group(1))
            continue
        if m_hd:
            flush()
            cur_no = to_int(m_hd.group(1))
            body = [line]           # เก็บทั้งบรรทัดไว้ เพราะเนื้อความเริ่มบรรทัดเดียวกับหัวข้อ
            continue
        if cur_no is None:
            preamble.append(line)   # ยังไม่ถึงข้อแรก ถือเป็นอารัมภบท
        else:
            body.append(line)
    flush()

    # อารัมภบทมีค่าของมันเอง เพราะเป็นที่อยู่ของ "อาศัยอํานาจตามความในมาตรา ..."
    # ซึ่งคือเส้นเชื่อมจากประกาศฉบับนี้กลับไปยัง พ.ร.บ. ที่ให้อำนาจ
    if preamble:
        text = " ".join(preamble).strip()
        if len(text) >= MIN_CHARS:
            pieces = split_oversize(text)
            for i, piece in enumerate(reversed(pieces)):
                part = (len(pieces) - i, len(pieces)) if len(pieces) > 1 else None
                chunks.insert(0, make_chunk(meta, "อารัมภบท", 0, "", "", piece, part))

    # ภาคผนวกเป็นตารางตัวชี้วัดและแบบฟอร์ม ไม่มีเลขข้อกำกับ จึงซอยตามขนาดอย่างเดียว
    # ลบหัวกระดาษที่ซ้ำทุกหน้าออกก่อน ไม่งั้นทุกก้อนจะขึ้นต้นเหมือนกันหมดจนแยกไม่ออก
    if annex_lines:
        text = " ".join(l for l in annex_lines
                        if not RE_ANNEX_RUNHEAD.match(l)).strip()
        for i, piece in enumerate(split_oversize(text), 1):
            if len(piece) >= MIN_CHARS:
                chunks.append(make_chunk(meta, "ภาคผนวก", i, "", "", piece))
    return chunks


# ส่วนที่ 5: ซอยก้อนที่ใหญ่เกินไป
# ข้อสุดท้ายของประกาศมักกวาดภาคผนวกและตารางตัวชี้วัดเข้ามาทั้งหมด
# วัดจริงกับ 26_protect ข้อ ๘ ได้ก้อนเดียว 49,370 ตัวอักษร ซึ่งยาวเกินกว่าจะ embed ได้ดี
# ซอยตามหมายเลขย่อย (๑) (๒) ก่อน เพราะเป็นรอยต่อตามธรรมชาติของตัวบท
# ถ้าไม่มีหมายเลขย่อยค่อยซอยตามช่องว่าง เพื่อไม่ให้ตัดกลางคำไทย
RE_SUBITEM = re.compile(r"(?=\((?:[๐-๙\d]{1,3})\)\s)")
RE_ANNEX = re.compile(r"(?=ภาคผนวก|ตารางที่\s)")


# หน้าที่: ซอยข้อความยาวเป็นท่อน ๆ ที่ไม่เกิน MAX_CHARS โดยพยายามตัดตามรอยต่อของตัวบท
# รับ: เนื้อความหนึ่งข้อ · คืน: รายการข้อความย่อย ถ้าไม่ยาวเกินก็คืนก้อนเดียว
def split_oversize(body: str) -> list[str]:
    if len(body) <= MAX_CHARS:
        return [body]
    parts = [p for p in RE_ANNEX.split(body) if p.strip()]
    if max((len(p) for p in parts), default=0) > MAX_CHARS:
        tmp = []
        for p in parts:
            tmp.extend(q for q in RE_SUBITEM.split(p) if q.strip())
        parts = tmp
    # รวมท่อนเล็ก ๆ ที่อยู่ติดกันกลับเข้าด้วยกัน จะได้ไม่แตกเป็นเศษเล็กเกินไป
    out, cur = [], ""
    for p in parts:
        if len(cur) + len(p) <= MAX_CHARS:
            cur += p
        else:
            if cur.strip():
                out.append(cur.strip())
            cur = p
            while len(cur) > MAX_CHARS:         # ท่อนเดียวยังยาวเกิน ตัดตามช่องว่าง
                cut = cur.rfind(" ", 0, MAX_CHARS)
                cut = cut if cut > MAX_CHARS // 2 else MAX_CHARS
                out.append(cur[:cut].strip())
                cur = cur[cut:]
    if cur.strip():
        out.append(cur.strip())
    return out


# ส่วนที่ 6: ประกอบ chunk หนึ่งก้อนพร้อม metadata และการอ้างอิง
# หน้าที่: ใส่หัวเรื่องกลับเข้าไปในข้อความ และดึงการอ้างอิงออกมาเก็บแยก
# รับ: metadata ของฉบับ, ชนิดหน่วย, เลขหน่วย, หมวด, ส่วนที่, เนื้อความ
# คืน: dict ที่มี id, text, metadata และรายการการอ้างอิง
def make_chunk(meta: dict, unit: str, no: int, muad: str, suan: str, body: str,
               part: tuple[int, int] | None = None) -> dict:
    title = meta["title"].strip()
    head_bits = [f"[{title}]"]
    if muad:
        head_bits.append(f"[{muad}]")
    if suan:
        head_bits.append(f"[{suan}]")
    label = "อารัมภบท" if unit == "อารัมภบท" else f"{unit} {no}"
    if part:
        label += f" (ตอนที่ {part[0]}/{part[1]})"   # บอกให้ชัดว่าเป็นส่วนหนึ่งของข้อที่ยาว
    # ข้อความที่จะเอาไปทำดัชนี มีหัวเรื่องนำหน้าเสมอ
    text = " ".join(head_bits) + f" {label}\n{body}"

    cites_matra = sorted({to_int(x) for x in RE_CITE_MATRA.findall(body)} - {None})
    cites_khor = sorted({to_int(x) for x in RE_CITE_KHOR.findall(body)} - {None, no})
    cites_noti = [t.strip()[:90] for t in RE_CITE_NOTI.findall(body)]

    cid = "%s::%s%s%s" % (meta["file"].replace(".pdf", ""),
                          "preamble" if unit == "อารัมภบท" else unit,
                          "" if unit == "อารัมภบท" else f"_{no}",
                          f"_p{part[0]}" if part else "")
    return {
        "id": cid,
        "text": text,
        "text_arabic": thai_to_arabic(text),   # สำเนาที่เลขเป็นอารบิก ไว้ให้ BM25 จับคู่เลขได้
        "doc_file": meta["file"],
        "doc_title": title,
        "law_type": meta["law_type"].strip(),
        "category": meta["category"].strip(),
        "url": meta["url"].strip(),
        "unit": unit,
        "number": no,
        "muad": muad,
        "suan": suan,
        "body": body,
        "n_chars": len(body),
        "part": list(part) if part else None,
        "cites_matra": cites_matra,
        "cites_khor": cites_khor,
        "cites_notification": cites_noti,
    }


# ส่วนที่ 7: จุดเริ่มทำงาน
# อ่าน manifest เลือกเฉพาะฉบับที่ข้อความสะอาด แล้วตัดทีละฉบับ
def main() -> int:
    man_path = os.path.join(LAW_DIR, "manifest.csv")
    man = list(csv.DictReader(open(man_path, encoding="utf-8")))

    rows, skipped = [], []
    for m in man:
        txt_path = os.path.join(LAW_DIR, "txt", m["file"].replace(".pdf", ".txt"))
        if not os.path.exists(txt_path):
            skipped.append((m["file"], "ไม่มีไฟล์ข้อความ"))
            continue
        raw = open(txt_path, encoding="utf-8", errors="ignore").read()
        # เกณฑ์คัดฉบับที่ใช้ได้: ต้องเจอคำที่มีวรรณยุกต์จริง ไม่งั้นแปลว่า PDF ตัวนั้น
        # แกะมาแล้ววรรณยุกต์หาย ตัวตัดจะหาหัวข้อไม่เจอและคำก็ผิดทั้งฉบับ
        raw = raw.replace(NIKHAHIT_AA, "\u0e33")      # เผื่อไฟล์เก่าที่ยังไม่ได้รวมอักขระ
        if not any(w in raw for w in ("ข้อ", "เล่ม", "แห่ง", "ผู้")):
            skipped.append((m["file"], "วรรณยุกต์หาย ฟอนต์ไม่ให้รหัส"))
            continue
        unit = "มาตรา" if m["file"].endswith("_act.pdf") else "ข้อ"
        got = split_document(raw, m, unit)
        if not got:
            skipped.append((m["file"], "ตัดไม่ได้สักก้อน"))
            continue
        rows.extend(got)

    # กันเหนียว ถ้ายังมี id ซ้ำหลงเหลือ ให้เติมเลขต่อท้าย
    # id ซ้ำอันตรายมาก เพราะขั้นสร้างบริบทจับคู่ด้วย id ถ้าซ้ำ บริบทจะถูกแชร์ข้ามก้อน
    seen: dict[str, int] = {}
    for r in rows:
        if r["id"] in seen:
            seen[r["id"]] += 1
            r["id"] = "%s_d%d" % (r["id"], seen[r["id"]])
        else:
            seen[r["id"]] = 0

    out = os.path.join(LAW_DIR, "chunks.json")
    json.dump(rows, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # รายงานผลให้เห็นว่าได้อะไรมาบ้าง จะได้ตรวจก่อนเอาไปทำดัชนี
    by_doc: dict[str, int] = {}
    for r in rows:
        by_doc[r["doc_file"]] = by_doc.get(r["doc_file"], 0) + 1
    print("ตัด chunk ได้ %d ก้อน จาก %d ฉบับ · id ไม่ซ้ำ %d"
          % (len(rows), len(by_doc), len({r["id"] for r in rows})))
    print("เขียนที่ %s\n" % out)
    print("%-17s %6s %9s %9s  %s" % ("ไฟล์", "chunk", "ตัวอักษร", "อ้างมาตรา", "ชื่อฉบับ"))
    for f in sorted(by_doc):
        sub = [r for r in rows if r["doc_file"] == f]
        print("%-17s %6d %9d %9d  %s" % (
            f.replace(".pdf", ""), len(sub), sum(r["n_chars"] for r in sub),
            sum(len(r["cites_matra"]) for r in sub), sub[0]["doc_title"][:48]))
    if skipped:
        print("\nข้ามไป %d ฉบับ" % len(skipped))
        for f, why in skipped:
            print("   %-17s %s" % (f.replace(".pdf", ""), why))
    sizes = sorted(r["n_chars"] for r in rows)
    if sizes:
        print("\nขนาด chunk  เล็กสุด %d  กลาง %d  ใหญ่สุด %d ตัวอักษร"
              % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
