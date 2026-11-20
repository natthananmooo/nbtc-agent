"""
lawrag/extract.py - แกะข้อความจาก PDF กฎหมาย แล้วคัดเฉพาะฉบับที่ข้อความใช้ได้จริง

ทำไมใช้ -raw ไม่ใช่ -layout
    -layout เติมช่องว่างเพื่อรักษาตำแหน่งคอลัมน์บนหน้ากระดาษ
    ช่องว่างพวกนั้นแทรกกลางคำไทยด้วย เช่น "ผู้ รั บ ใบอนุ ญ าต"
    ซึ่งทำให้ตัวตัดคำไทยพังทั้งระบบ และ BM25 นับคำผิดหมด
    วัดจริงกับ 26_protect: -layout มีช่องว่างกลางคำ 6 จุด -raw มี 0 จุด
    และ -raw เจอคำว่า ผู้รับใบอนุญาต 42 ครั้ง ส่วน -layout เจอ 35 ครั้ง

ทำไมต้องรวมอักขระสระอำ
    PDF พวกนี้เข้ารหัส "ำ" เป็นนิคหิต U+0E4D ตามด้วยสระอา U+0E32 สองตัวแยกกัน
    ค้นคำว่า "สำนักงาน" จึงไม่เจอสักครั้งทั้งที่คำนั้นอยู่เต็มไปหมด
    ต้องรวมเป็นอักขระเดียว U+0E33 ก่อนทำอะไรต่อ

ทำไมต้องซ่อมสระอำที่หายไปทั้งตัว
    บางฉบับฝังฟอนต์ที่ไม่มีรหัสของนิคหิตเลย แกะออกมาแล้วเหลือแค่สระอา
    สำนักงาน กลายเป็น สานักงาน · กำหนด กลายเป็น กาหนด · ดำเนินการ กลายเป็น ดาเนินการ
    วัดจริงทั้งคลัง เจอ 965 ตำแหน่งใน 16 ไฟล์ บางไฟล์ไม่มีสระอำที่ถูกต้องเลยแม้ตัวเดียว
    กระทบ BM25 ตรง ๆ เพราะคนค้นพิมพ์ สำนักงาน ที่ถูก แล้วไม่แมตช์กับคลังที่เพี้ยน

    ซ่อมโดยไม่เดา สร้างตารางแก้จากพจนานุกรมไทยของ pythainlp 62,106 คำ ด้วยกฎสองชั้น
      ชั้นแรก  เอาคำที่มีสระอำมาสร้างรูปเพี้ยนโดยเปลี่ยน ำ เป็น า
               ถ้ารูปเพี้ยนเป็นคำจริงด้วย ให้ตัดทิ้ง ห้ามแตะ เช่น ทำ/ทา คำ/คา นำ/นา
      ชั้นสอง  ถ้ารูปเพี้ยนเป็นต้นคำของคำจริงอื่น ก็ตัดทิ้งอีก
               เช่น ประจำ ให้รูป ประจา แต่ ประจาน เป็นคำจริง แทนที่แล้วจะได้ ประจำน
    เหลือคู่ที่ปลอดภัย 3,363 คู่ เก็บใน lawrag/sara_am_fix.json
    แทนที่จากคำยาวไปสั้น กันการแทนที่ซ้อนกัน

เกณฑ์คัดฉบับที่ใช้ได้
    PDF เก่าบางฉบับฝังฟอนต์ที่ไม่มีรหัสของวรรณยุกต์ แกะออกมาแล้ว ข้อ กลายเป็น ขอ
    ลองทั้ง pdftotext สามโหมดและ pypdf แล้วได้ผลเหมือนกัน แปลว่าวรรณยุกต์
    ไม่ได้อยู่ในไฟล์ตั้งแต่แรก ไม่ใช่เครื่องมือแกะผิด จึงกู้ไม่ได้นอกจาก OCR
    ทดสอบด้วยคำที่ต้องมีวรรณยุกต์ ข้อ เล่ม แห่ง ถ้าไม่เจอสักคำ แปลว่าฉบับนั้นเสีย

ไฟล์เข้า : data/law/pdf/*.pdf และ data/law/manifest.csv
ไฟล์ออก : data/law/txt/*.txt เฉพาะฉบับที่ผ่านเกณฑ์ และ data/law/extract_report.csv
"""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys

# ส่วนที่ 1: ค่าคงที่
BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))

NIKHAHIT = "ํ"   # ◌ํ
SARA_AA = "า"    # า
SARA_AM = "ำ"    # ◌ำ ตัวที่ถูกต้อง

# คำที่ต้องมีวรรณยุกต์หรือการันต์ ใช้ทดสอบว่าฟอนต์ให้รหัสครบไหม
PROBE_WORDS = ("ข้อ", "เล่ม", "แห่ง", "ผู้")

# ตารางซ่อมสระอำที่หายทั้งตัว สร้างจากพจนานุกรมไทยด้วยกฎสองชั้น ดูคำอธิบายหัวไฟล์
SARA_AM_FIX_PATH = os.path.join(BASE, "sara_am_fix.json")


# ส่วนที่ 1.1: เตรียมตัวแทนที่สระอำ
# หน้าที่: โหลดตารางคู่แก้ แล้วคอมไพล์เป็น regex เดียวเรียงจากคำยาวไปสั้น
# รับ: ไม่รับ · คืน: (regex, dict) หรือ (None, {}) ถ้าไม่มีไฟล์ตาราง
def _load_sara_am_fix():
    if not os.path.exists(SARA_AM_FIX_PATH):
        print("เตือน ไม่พบ sara_am_fix.json จะไม่ซ่อมสระอำที่หายทั้งตัว", file=sys.stderr)
        return None, {}
    with open(SARA_AM_FIX_PATH, encoding="utf-8") as f:
        table = json.load(f)
    # เรียงยาวก่อน ให้ การกำกับ ชนะ กำกับ ไม่ให้ซ่อมทับกันแล้วได้คำพิการ
    keys = sorted(table, key=len, reverse=True)
    return re.compile("|".join(re.escape(k) for k in keys)), table


SARA_AM_RE, SARA_AM_TABLE = _load_sara_am_fix()


# ส่วนที่ 2: ทำความสะอาดระดับอักขระ
# หน้าที่: รวมนิคหิตกับสระอาเป็นสระอำ และตัดช่องว่างซ้อน
# รับ: ข้อความดิบ · คืน: ข้อความที่อักขระถูกต้องตามมาตรฐาน
def normalize(t: str) -> str:
    # ฉบับรวมเล่มของ พ.ร.บ. วางสระอำเป็นสองกราฟิก ตัวหน้าไม่มีรหัส Unicode
    # pdftotext จึงคืนเป็นอักขระแทนที่ U+FFFD คั่นอยู่ เช่น ก ำ U+FFFD ก ั บ
    # ลบตัวคั่นทิ้งแล้วคำกลับมาถูกทันที วัดจริงกับ 01_act คำว่า กำกับ จาก 101 เป็น 270 ครั้ง
    t = t.replace("\ufffd", "")
    t = t.replace(NIKHAHIT + SARA_AA, SARA_AM)  # ◌ํ + า -> ◌ำ
    # ปัญหาแบบที่สาม นิคหิตหายทั้งตัว เหลือแค่สระอา ซ่อมด้วยตารางจากพจนานุกรม
    if SARA_AM_RE is not None:
        t = SARA_AM_RE.sub(lambda m: SARA_AM_TABLE[m.group(0)], t)
    t = t.replace(" ", " ")                 # ช่องว่างไม่ตัดบรรทัด
    return t


# ส่วนที่ 3: แกะข้อความหนึ่งฉบับ
# หน้าที่: เรียก pdftotext โหมด raw แล้วรวมอักขระให้ถูกต้อง
# รับ: พาธของ PDF · คืน: ข้อความที่พร้อมใช้ หรือสตริงว่างถ้าแกะไม่ได้
def extract(pdf_path: str) -> str:
    if open(pdf_path, "rb").read(4) != b"%PDF":
        return ""  # บางไฟล์เป็นหน้าเว็บที่ถูกเซฟมาเป็นนามสกุล pdf
    r = subprocess.run(["pdftotext", "-enc", "UTF-8", "-raw", pdf_path, "-"],
                       capture_output=True, text=True, timeout=180)
    return normalize(r.stdout)


# ส่วนที่ 4: ตรวจคุณภาพข้อความ
# หน้าที่: บอกว่าฉบับนี้ใช้ต่อได้หรือไม่ และถ้าไม่ได้เพราะอะไร
# รับ: ข้อความที่แกะแล้ว · คืน: (ผ่านหรือไม่, เหตุผล, จำนวนอักษรไทย)
def judge(text: str) -> tuple[bool, str, int]:
    thai = sum(1 for c in text if "฀" <= c <= "๿")
    if thai < 200:
        return False, "ไม่มีข้อความ น่าจะเป็นภาพสแกน", thai
    if not any(w in text for w in PROBE_WORDS):
        return False, "วรรณยุกต์หาย ฟอนต์ไม่ให้รหัส", thai
    return True, "ใช้ได้", thai


# ส่วนที่ 5: จุดเริ่มทำงาน
def main() -> int:
    man = list(csv.DictReader(open(os.path.join(LAW_DIR, "manifest.csv"), encoding="utf-8")))
    out_dir = os.path.join(LAW_DIR, "txt")
    os.makedirs(out_dir, exist_ok=True)

    report, keep = [], 0
    for m in man:
        pdf = os.path.join(LAW_DIR, "pdf", m["file"])
        if not os.path.exists(pdf):
            report.append((m["file"], "ไม่พบไฟล์", 0, m["title"]))
            continue
        text = extract(pdf)
        ok, why, thai = judge(text)
        txt = os.path.join(out_dir, m["file"].replace(".pdf", ".txt"))
        if ok:
            open(txt, "w", encoding="utf-8").write(text)
            keep += 1
        elif os.path.exists(txt):
            os.rename(txt, txt + ".rejected")  # ลบไม่ได้ในโฟลเดอร์นี้ จึงเปลี่ยนชื่อแทน
        report.append((m["file"], why, thai, m["title"]))

    with open(os.path.join(LAW_DIR, "extract_report.csv"), "w",
              encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["file", "result", "thai_chars", "title"])
        w.writerows(report)

    print("แกะข้อความได้ %d ฉบับ จากทั้งหมด %d ฉบับ\n" % (keep, len(man)))
    print("%-17s %-30s %9s  %s" % ("ไฟล์", "ผล", "อักษรไทย", "ชื่อฉบับ"))
    for f, why, n, title in report:
        print("%-17s %-30s %9s  %s" % (f.replace(".pdf", ""), why,
                                       format(n, ",") if n else "-", title.strip()[:42]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
