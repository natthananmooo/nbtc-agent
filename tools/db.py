"""
tools/db.py - ชั้นเข้าถึงฐานข้อมูล

หน้าที่เดียวของไฟล์นี้คือทำให้ "เขียนฐานข้อมูลไม่ได้" โดยไม่ขึ้นกับการตัดสินใจของ LLM
LLM ไม่เคยส่ง SQL เข้ามาที่นี่ ส่งมาแค่ชื่อคอลัมน์กับค่าที่ต้องการกรอง
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import closing
from pathlib import Path

_DEFAULT = Path(__file__).resolve().parent.parent / "data" / "complaints.db"
DB_PATH = os.environ.get("COMPLAINTS_DB", str(_DEFAULT))

TABLE = "complaints"

# --------------------------------------------------------------- whitelist
# ชั้นที่ 2 ของกลไกอ่านอย่างเดียว
# ชื่อคอลัมน์ทุกตัวที่จะโผล่ใน SQL ต้องผ่าน dict นี้ก่อน
# ส่วนค่าที่ผู้ใช้กรอก ส่งผ่าน parameter (?) เสมอ ไม่เคยต่อสตริงเข้า SQL

DIMENSIONS: dict[str, str] = {
    "year":           "ปีที่รับเรื่อง (ค.ศ.)",
    "month":          "เดือนที่รับเรื่อง 1-12",
    "provider":       "ผู้ให้บริการ เก็บเป็นชื่อนิติบุคคลเต็ม",
    "status":         "สถานะเรื่อง",
    "channel":        "ช่องทางที่รับเรื่อง",
    "region":         "ภูมิภาค",
    "province":       "จังหวัด",
    "district":       "อำเภอหรือเขต",
    "cat_level1":     "หมวดหมู่ระดับ 1",
    "cat_level2":     "หมวดหมู่ระดับ 2",
    "service_type":   "ประเภทบริการ",
    "problem_group":  "กลุ่มปัญหา",
    "problem_detail": "รายละเอียดปัญหา",
}

DATE_COLUMNS = ("received_date", "closed_date")

MEASURES: dict[str, str] = {
    "count":             "จำนวนเรื่องร้องเรียน",
    "avg_days_to_close": "ระยะเวลาปิดเรื่องเฉลี่ย หน่วยเป็นวัน",
    "share_pct":         "สัดส่วนเป็นร้อยละของผลลัพธ์ทั้งหมด",
}


class SchemaError(ValueError):
    """ชื่อคอลัมน์หรือ metric ไม่อยู่ใน whitelist"""


def check_dimension(name: str) -> str:
    if name not in DIMENSIONS:
        raise SchemaError(
            "ไม่รู้จักคอลัมน์ %r - ที่ใช้ได้คือ %s" % (name, ", ".join(DIMENSIONS))
        )
    return name


def check_measure(name: str) -> str:
    if name not in MEASURES:
        raise SchemaError(
            "ไม่รู้จัก metric %r - ที่ใช้ได้คือ %s" % (name, ", ".join(MEASURES))
        )
    return name


# -------------------------------------------------------------- connection
def connect() -> sqlite3.Connection:
    """
    ชั้นที่ 3 ของกลไกอ่านอย่างเดียว
    mode=ro ทำให้ sqlite driver ปฏิเสธคำสั่งเขียนตั้งแต่ก่อนแตะข้อมูล
    """
    if not Path(DB_PATH).exists():
        raise FileNotFoundError(
            "ไม่พบฐานข้อมูลที่ %s - รัน 01_fetch_explore.py แล้วตามด้วย 02_build_db.py ก่อน"
            % DB_PATH
        )
    con = sqlite3.connect("file:%s?mode=ro" % DB_PATH, uri=True)
    con.row_factory = sqlite3.Row
    return con


def fetch(sql: str, params: tuple = ()) -> list[dict]:
    with closing(connect()) as con:
        return [dict(r) for r in con.execute(sql, params)]


# ------------------------------------------------------------- ตัวช่วยอ่าน
def distinct_values(column: str) -> list[tuple[str, int]]:
    """คืน (ค่า, จำนวนแถว) เรียงจากมากไปน้อย ใช้โดย resolve_filter_values"""
    col = check_dimension(column)          # ปลอดภัยที่จะใส่ใน f-string หลังผ่าน whitelist
    rows = fetch(
        "SELECT %s AS v, COUNT(*) AS n FROM %s "
        "WHERE %s IS NOT NULL GROUP BY %s ORDER BY n DESC" % (col, TABLE, col, col)
    )
    return [(r["v"], r["n"]) for r in rows]


def null_rate(column: str) -> float:
    """สัดส่วนแถวที่คอลัมน์นี้ว่าง ใช้สร้าง warnings ให้ผู้ใช้รู้ข้อจำกัด"""
    col = check_dimension(column)
    r = fetch(
        "SELECT COUNT(*) AS total, SUM(%s IS NULL) AS missing FROM %s" % (col, TABLE)
    )[0]
    return 0.0 if not r["total"] else r["missing"] / r["total"]


def total_rows() -> int:
    return fetch("SELECT COUNT(*) AS n FROM %s" % TABLE)[0]["n"]


# ------------------------------------------------------------ ทดสอบด้วยตัวเอง
if __name__ == "__main__":
    print("ฐานข้อมูล :", DB_PATH)
    print("จำนวนแถว  :", total_rows())

    print("\nทดสอบว่าเขียนไม่ได้จริง")
    for sql in (
        "DELETE FROM complaints",
        "UPDATE complaints SET provider = 'x'",
        "DROP TABLE complaints",
        "CREATE TABLE evil (a INT)",
    ):
        try:
            with closing(connect()) as con:
                con.execute(sql)
            print("  ไม่ผ่าน! รันได้ :", sql)
        except sqlite3.OperationalError as e:
            print("  บล็อก :", sql.split()[0], "->", e)

    print("\nทดสอบ whitelist")
    try:
        distinct_values("complaint_no; DROP TABLE complaints")
    except SchemaError as e:
        print("  บล็อก :", e)
