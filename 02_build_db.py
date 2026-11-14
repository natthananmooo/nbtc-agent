"""
02_build_db.py — สร้างฐานข้อมูล SQLite แบบ read-only จากข้อมูลเรื่องร้องเรียน กสทช.
================================================================================
หลักการ: ไม่แก้ไขข้อมูลต้นฉบับ
  - raw_complaints.json  ไม่ถูกแตะ
  - คอลัมน์ทุกตัวคัดลอกค่าจากต้นฉบับตรง ๆ
  - ปีเก็บเป็น พ.ศ. เหมือนที่ กสทช. เผยแพร่ ไม่แปลงเป็น ค.ศ.
  - วันที่จัดรูปใหม่เป็น ปปปป-ดด-วว เพื่อให้ SQL เรียงและเทียบได้ แต่ปียังเป็น พ.ศ.
  - เก็บสตริงวันที่ต้นฉบับไว้ด้วยใน received_date_raw และ closed_date_raw
  - ไม่จัดกลุ่ม ไม่ตีความ ไม่เพิ่มคอลัมน์ที่เป็นการตัดสินใจแทนผู้ใช้

รัน:
    python 02_build_db.py

ได้ไฟล์:
    data/complaints.db    ฐานข้อมูล (read-only, chmod 444)
"""

import json
import re
import shutil
import sqlite3
import stat
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")
RAW = DATA_DIR / "raw_complaints.json"
DB = DATA_DIR / "complaints.db"
TABLE = "complaints"

# ----------------------------------------------------------------------------
# วันที่ไทย "05-ม.ค.-2552" -> "2552-01-05"
# เหตุผลที่ต้องจัดรูปใหม่: SQL เรียงและเทียบวันที่จากรูปแบบเดิมไม่ได้
# แต่ปียังเป็น พ.ศ. เหมือนต้นฉบับ ไม่แปลงเป็น ค.ศ.
# ----------------------------------------------------------------------------
TH_MONTHS = {"ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
             "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}
TH_DIGITS = str.maketrans("0123456789", "0123456789")
TH_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def parse_thai_date(s):
    if not s or not str(s).strip():
        return None
    s = str(s).strip().translate(TH_DIGITS)
    m = re.match(r"^(\d{1,2})-([^-]+)-(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if not m:
        return None
    mon = TH_MONTHS.get(m.group(2).strip())
    if not mon:
        return None
    be = int(m.group(3))
    # ตรวจความถูกต้องของวันด้วยปฏิทิน ค.ศ. เสมอ
    # เพราะวันอธิกสุรทินอิงปี ค.ศ. เช่น 29 ก.พ. 2559 มีจริง (ค.ศ. 2016 เป็นปีอธิกสุรทิน)
    # แต่ datetime(2559, 2, 29) จะ error เพราะ 2559 หารสี่ไม่ลงตัว
    # จึงเก็บเป็น datetime ค.ศ. ไว้คำนวณ แล้วบวก 543 ตอนแสดงผล
    ce = be - 543 if be > 2300 else be
    try:
        return datetime(ce, mon, int(m.group(1)),
                        int(m.group(4) or 0), int(m.group(5) or 0))
    except ValueError:
        return None


def build_dataframe():
    records = json.loads(RAW.read_text(encoding="utf-8"))
    src = pd.DataFrame(records)

    # ตัดช่องว่างหัวท้าย (ต้นฉบับมี "ปิด " ที่มีช่องว่างท้าย ทำให้ GROUP BY แตกเป็น 2 กลุ่ม)
    for c in src.columns:
        if src[c].dtype == object:
            src[c] = src[c].astype(str).str.strip().replace({"": None, "None": None, "nan": None})

    def _ok(d):
        return d is not None and not pd.isna(d)

    rec = src["tci_no_datestamp"].apply(parse_thai_date)
    clo = src["close_date"].apply(parse_thai_date)

    # ปี พ.ศ. เกินช่วงที่ pandas datetime64 รองรับ (สูงสุด ค.ศ. 2262)
    # จึงลบกันด้วย datetime ของ Python ตรง ๆ
    days = [ (c - r).days if (_ok(r) and _ok(c)) else None for r, c in zip(rec, clo) ]

    def _iso(d):
        # ปปปป-ดด-วว โดยปีเป็น พ.ศ. (datetime ข้างในเป็น ค.ศ. จึงบวก 543 ตอนนี้)
        return "%04d-%02d-%02d" % (d.year + 543, d.month, d.day) if _ok(d) else None

    df = pd.DataFrame()
    df["complaint_no"] = src["tci_no"]
    df["received_date"] = rec.apply(_iso)
    df["closed_date"] = clo.apply(_iso)
    df["received_date_raw"] = src["tci_no_datestamp"]
    df["closed_date_raw"] = src["close_date"]
    df["year"] = rec.apply(lambda d: int(d.year) + 543 if _ok(d) else None)
    df["month"] = rec.apply(lambda d: int(d.month) if _ok(d) else None)
    df["days_to_close"] = days
    df["provider"] = src["provider_id"]
    df["status"] = src["request_status"]
    df["channel"] = src["channel_type"]
    df["region"] = src["geo_name"]
    df["province"] = src["impact_area"]
    df["district"] = src["impact_area2"]

    # แตก category ที่ต้นฉบับเก็บรวมในช่องเดียว คั่นด้วย " - "
    # เก็บ category_full ไว้ด้วย ดูค่าต้นฉบับได้เสมอ
    parts = src["category_id"].fillna("").apply(lambda s: re.split(r"\s*-\s*", str(s)))
    pick = lambda p, i: p[i].strip() if len(p) > i and p[i].strip() else None
    df["cat_level1"] = parts.apply(lambda p: pick(p, 0))
    df["cat_level2"] = parts.apply(lambda p: pick(p, 1))
    df["service_type"] = parts.apply(lambda p: pick(p, 2))
    df["problem_group"] = parts.apply(lambda p: pick(p, 3))
    df["problem_detail"] = parts.apply(lambda p: pick(p, 4))
    df["category_full"] = src["category_id"]

    # ปี เดือน และจำนวนวัน ต้องเป็นจำนวนเต็ม ไม่ใช่ทศนิยม
    # pandas เปลี่ยนเป็น float อัตโนมัติเมื่อมีค่าว่าง จึงบังคับเป็น Int64 ที่รองรับค่าว่าง
    for c in ("year", "month", "days_to_close"):
        df[c] = pd.array(df[c], dtype="Int64")

    return df


def write_db(df):
    # สร้างนอกโฟลเดอร์ปลายทางก่อน เพราะบางระบบไฟล์ลบ journal ของ sqlite ไม่ได้
    # แล้วค่อยคัดลอกทับ การคัดลอกทับเป็นการเขียน ไม่ใช่การลบ จึงผ่านเสมอ
    tmp = Path(tempfile.gettempdir()) / "complaints_build.db"
    if tmp.exists():
        tmp.unlink()

    conn = sqlite3.connect(tmp)
    df.to_sql(TABLE, conn, index=False, if_exists="replace")
    cur = conn.cursor()
    cur.execute(f"DROP INDEX IF EXISTS idx_provider_group")
    for col in ["year", "province", "provider", "service_type", "problem_group", "region"]:
        cur.execute(f"CREATE INDEX IF NOT EXISTS idx_{col} ON {TABLE}({col})")
    conn.commit()
    conn.execute("VACUUM")
    conn.close()

    DATA_DIR.mkdir(exist_ok=True)
    if DB.exists():
        DB.chmod(stat.S_IWUSR | stat.S_IRUSR)
    shutil.copyfile(tmp, DB)
    tmp.unlink()

    DB.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    print(f"สร้างฐานข้อมูล: {DB}  ({DB.stat().st_size / 1e6:.1f} MB, permission 444)")


def verify():
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
    print(f"\nแถวทั้งหมด: {cur.fetchone()[0]:,}")
    cur.execute(f"PRAGMA table_info({TABLE})")
    cols = [r[1] for r in cur.fetchall()]
    print(f"คอลัมน์ ({len(cols)}): {', '.join(cols)}")
    cur.execute(f"SELECT COUNT(DISTINCT provider) FROM {TABLE}")
    print(f"ผู้ให้บริการ: {cur.fetchone()[0]} ชื่อ (ตามต้นฉบับ ไม่จัดกลุ่ม)")
    cur.execute(f"SELECT MIN(year), MAX(year) FROM {TABLE} WHERE year IS NOT NULL")
    a, b = cur.fetchone()
    print(f"ปี (พ.ศ. ตามต้นฉบับ): {a} ถึง {b}")
    cur.execute(f"SELECT received_date_raw, received_date, year FROM {TABLE} "
                f"WHERE received_date_raw IS NOT NULL LIMIT 3")
    print("ตัวอย่าง ต้นฉบับ -> ที่เก็บ:")
    for r in cur.fetchall():
        print(f"  {r[0]:24} -> {r[1]}  (year={r[2]})")

    print("\n--- ทดสอบ read-only ---")
    for sql in [f"DELETE FROM {TABLE}", f"UPDATE {TABLE} SET province='x'",
                f"DROP TABLE {TABLE}", "CREATE TABLE evil (id INT)"]:
        try:
            cur.execute(sql)
            print(f"  !! ไม่ถูกบล็อก: {sql}")
        except sqlite3.OperationalError as e:
            print(f"  {sql[:30]:32} -> {e}")
    conn.close()


if __name__ == "__main__":
    df = build_dataframe()
    print(f"เตรียมข้อมูล {len(df):,} แถว")
    write_db(df)
    verify()
