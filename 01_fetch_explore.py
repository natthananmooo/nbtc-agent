"""
01_fetch_explore.py — ดึงข้อมูลเรื่องร้องเรียน กสทช. + สำรวจก่อนออกแบบ schema
================================================================================
ชุดข้อมูล: สถิติเรื่องร้องเรียนในกิจการโทรคมนาคม
resource_id: 5f7f96f3-332a-4d4a-a9d0-03fd55e6d736

รัน:
    pip install requests pandas
    python 01_fetch_explore.py

ได้ไฟล์:
    data/raw_complaints.json   ข้อมูลดิบทั้งหมด (เผื่อ API ล่ม จะได้ไม่ต้องโหลดใหม่)
    data/complaints_clean.csv  ข้อมูลที่ทำความสะอาดแล้ว
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path

import requests
import pandas as pd

BASE = "https://datacatalog.nbtc.go.th"
RESOURCE_ID = "5f7f96f3-332a-4d4a-a9d0-03fd55e6d736"
PAGE = 10000                      # เพดานของ CKAN ต่อ 1 request
DATA_DIR = Path("data")
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

# ----------------------------------------------------------------------------
# ตัวแปลงวันที่ไทย (ทดสอบแล้วกับค่าจริง + เลขไทย + ค่าว่าง)
# ----------------------------------------------------------------------------
TH_MONTHS = {"ม.ค.": 1, "ก.พ.": 2, "มี.ค.": 3, "เม.ย.": 4, "พ.ค.": 5, "มิ.ย.": 6,
             "ก.ค.": 7, "ส.ค.": 8, "ก.ย.": 9, "ต.ค.": 10, "พ.ย.": 11, "ธ.ค.": 12}
TH_DIGITS = str.maketrans("๐๑๒๓๔๕๖๗๘๙", "0123456789")


def parse_thai_date(s):
    """'05-ม.ค.-2552 00:00' -> datetime(2009,1,5) ; None ถ้าแปลงไม่ได้"""
    if not s or not str(s).strip():
        return None
    s = str(s).strip().translate(TH_DIGITS)
    m = re.match(r"^(\d{1,2})-([^-]+)-(\d{4})(?:\s+(\d{1,2}):(\d{2}))?", s)
    if not m:
        return None
    mon = TH_MONTHS.get(m.group(2).strip())
    if not mon:
        return None
    year_be = int(m.group(3))
    year_ce = year_be - 543 if year_be > 2300 else year_be
    try:
        return datetime(year_ce, mon, int(m.group(1)),
                        int(m.group(4) or 0), int(m.group(5) or 0))
    except ValueError:
        return None


# ----------------------------------------------------------------------------
# 1. ดึงข้อมูลทั้งหมด (แบ่งหน้า)
# ----------------------------------------------------------------------------
def fetch_all():
    DATA_DIR.mkdir(exist_ok=True)
    cache = DATA_DIR / "raw_complaints.json"
    if cache.exists():
        print(f"ใช้ข้อมูลที่โหลดไว้แล้ว: {cache}  (ลบไฟล์นี้ถ้าอยากโหลดใหม่)")
        return json.loads(cache.read_text(encoding="utf-8"))

    url = f"{BASE}/api/3/action/datastore_search"
    records, offset, total = [], 0, None

    while True:
        r = requests.get(url, params={"resource_id": RESOURCE_ID,
                                      "limit": PAGE, "offset": offset},
                         headers=HEADERS, timeout=120)
        r.raise_for_status()
        res = r.json()["result"]
        if total is None:
            total = res.get("total")
            print(f"ทั้งหมด {total:,} แถว")
        batch = res["records"]
        records.extend(batch)
        print(f"  โหลดแล้ว {len(records):,}/{total:,}")
        if len(batch) < PAGE or (total and len(records) >= total):
            break
        offset += PAGE
        time.sleep(0.5)                       # ไม่ยิงถี่เกินไป

    cache.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    print(f"บันทึก: {cache}")
    return records


# ----------------------------------------------------------------------------
# 2. ทำความสะอาด
# ----------------------------------------------------------------------------
def clean(records):
    df = pd.DataFrame(records)

    # ตัดช่องว่างหัวท้ายทุกคอลัมน์ข้อความ (แก้ "ปิด " ที่มีช่องว่างท้าย)
    for c in df.columns:
        if df[c].dtype == object:
            df[c] = df[c].astype(str).str.strip().replace({"": None, "None": None})

    # แปลงวันที่
    df["received_date"] = df["tci_no_datestamp"].apply(parse_thai_date)
    df["closed_date"] = df["close_date"].apply(parse_thai_date)
    df["year"] = df["received_date"].apply(lambda d: d.year if d else None)
    df["month"] = df["received_date"].apply(lambda d: d.month if d else None)
    df["days_to_close"] = (
        pd.to_datetime(df["closed_date"]) - pd.to_datetime(df["received_date"])
    ).dt.days

    # แตก category 5 ระดับ ("A - B - C - D - E")
    parts = df["category_id"].fillna("").apply(lambda x: re.split(r"\s*-\s*", str(x)))
    for i in range(5):
        df[f"cat_{i+1}"] = parts.apply(lambda p, i=i: p[i].strip() if len(p) > i and p[i].strip() else None)

    return df


# ----------------------------------------------------------------------------
# 3. สำรวจ — ดูว่าข้อมูลตอบคำถามแบบไหนได้
# ----------------------------------------------------------------------------
def explore(df):
    print("\n" + "=" * 72)
    print(f"  ขนาดข้อมูล: {df.shape[0]:,} แถว  x  {df.shape[1]} คอลัมน์")
    print("=" * 72)

    print("\n--- ค่าว่างแต่ละคอลัมน์ ---")
    na = df.isna().sum()
    for c in df.columns:
        pct = na[c] / len(df) * 100
        flag = "  <-- ว่างเยอะ" if pct > 30 else ""
        print(f"  {c:22} ว่าง {na[c]:>7,} ({pct:5.1f}%){flag}")

    print("\n--- ช่วงเวลา ---")
    if df["received_date"].notna().any():
        print(f"  วันรับเรื่อง : {df['received_date'].min()}  ถึง  {df['received_date'].max()}")
        print(f"  แปลงวันที่สำเร็จ: {df['received_date'].notna().sum():,}/{len(df):,} แถว")
    print(f"\n  จำนวนต่อปี:")
    print(df["year"].value_counts().sort_index().to_string())

    print("\n--- จำนวนค่าไม่ซ้ำ (ดูว่าใช้เป็นมิติได้ไหม) ---")
    for c in ["provider_id", "request_status", "channel_type",
              "geo_name", "impact_area", "impact_area2",
              "cat_1", "cat_2", "cat_3", "cat_4", "cat_5"]:
        if c in df.columns:
            print(f"  {c:16} {df[c].nunique():>5} ค่า")

    print("\n--- ค่าที่พบบ่อย (top 8 ต่อคอลัมน์) ---")
    for c in ["provider_id", "request_status", "channel_type", "geo_name",
              "impact_area", "cat_3", "cat_4"]:
        if c in df.columns:
            print(f"\n[{c}]")
            print(df[c].value_counts().head(8).to_string())

    if df["days_to_close"].notna().any():
        print("\n--- ระยะเวลาปิดเรื่อง (วัน) ---")
        print(df["days_to_close"].describe().to_string())
        neg = (df["days_to_close"] < 0).sum()
        if neg:
            print(f"  ผิดปกติ: ปิดก่อนรับเรื่อง {neg} แถว")


if __name__ == "__main__":
    records = fetch_all()
    df = clean(records)
    explore(df)

    out = DATA_DIR / "complaints_clean.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"\nบันทึกข้อมูลที่สะอาดแล้ว: {out}")
