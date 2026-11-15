"""
tools/query.py - สืบค้นฐานข้อมูลแบบอ่านอย่างเดียว

LLM ไม่เคยเขียน SQL เอง ส่งมาแค่พารามิเตอร์ที่มีโครงสร้าง
SQL ถูกประกอบขึ้นที่นี่จากชื่อคอลัมน์ที่ผ่าน whitelist แล้วเท่านั้น
ค่าที่ผู้ใช้กรอกส่งผ่าน parameter (?) ทุกตัว

คืน sql_used กลับไปเสมอ เพื่อให้
  1. ผู้ใช้เห็นแหล่งที่มาของตัวเลข
  2. ส่งเข้า Langfuse เป็น context สำหรับวัด faithfulness
"""

from __future__ import annotations

from . import db

MIN_DAYS = 0
# เพดานนี้ไม่ได้มีไว้จำกัดคำถาม แต่มีไว้กันค่าที่เป็นไปไม่ได้
# ตั้งจากจำนวนกลุ่มจริงสูงสุดที่วัดได้ในฐานข้อมูล
#   district, year               4,924 กลุ่ม  มากที่สุดที่เจอ
#   provider, problem_group, year 1,691 กลุ่ม
#   province, year                1,168 กลุ่ม
# ตั้ง 50000 ซึ่งมากกว่าหลายเท่า คำถามปกติจึงไม่มีทางชน
MAX_LIMIT = 50000          # days_to_close ติดลบ ตัดออกตอนคำนวณค่าเฉลี่ย
NULL_WARN_AT = 0.05   # คอลัมน์ที่ใช้กรองแล้วว่างเกินนี้ ต้องเตือน


def _to_be(year):
    """
    ฐานข้อมูลเก็บปีเป็น พ.ศ. ตามที่ กสทช. เผยแพร่ (2552 ถึง 2569)
    ถ้าผู้ใช้ส่ง ค.ศ. มา (น้อยกว่า 2400) แปลงขึ้นเป็น พ.ศ. ให้
    """
    try:
        y = int(year)
    except (TypeError, ValueError):
        return year, False
    return (y + 543, True) if y < 2400 else (y, False)


def _filter_clauses(filters, date_from, date_to, warnings=None):
    """ชื่อคอลัมน์ผ่าน whitelist ค่าผ่าน ? เสมอ"""
    clauses, params = [], []
    for col, val in (filters or {}).items():
        c = db.check_dimension(col)
        vals = val if isinstance(val, (list, tuple)) else [val]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
        if c == "year":
            converted = [_to_be(v) for v in vals]
            if any(flag for _, flag in converted) and warnings is not None:
                warnings.append(
                    "ผู้ใช้ระบุปีเป็น ค.ศ. %s ฐานข้อมูลเก็บเป็น พ.ศ. จึงค้นด้วย %s"
                    % (vals, [y for y, _ in converted]))
            vals = [y for y, _ in converted]
        clauses.append("%s IN (%s)" % (c, ",".join("?" * len(vals))))
        params.extend(vals)
    if date_from:
        clauses.append("received_date >= ?")
        params.append(date_from)
    if date_to:
        clauses.append("received_date <= ?")
        params.append(date_to)
    return clauses, params


def _sql_where(clauses):
    return (" WHERE " + " AND ".join(clauses)) if clauses else ""


def query_complaints(
    metric: str = "count",
    group_by: list | None = None,
    filters: dict | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    order_by: str | None = None,
    limit: int = 100,
) -> dict:
    """
    metric    : count | avg_days_to_close | share_pct
    group_by  : คอลัมน์ที่จะจัดกลุ่ม เช่น ["year"] หรือ ["province"]
    filters   : {"year": [2021], "provider": ["บริษัท ..."]}
    date_from : "YYYY-MM-DD" กรองจากวันที่รับเรื่อง
    """
    m = db.check_measure(metric)

    # ตรวจช่วงของ limit ไว้ในฟังก์ชันเอง ไม่ฝากไว้กับ type hint
    # ถ้าปล่อยให้ชั้น protocol ปฏิเสธก่อนเข้ามา LLM จะได้ error ดิบที่แก้ต่อไม่ได้
    # เขียนเองแบบนี้ LLM จะได้ how_to_fix กลับไปแล้วเรียกใหม่ได้
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise db.SchemaError("limit ต้องเป็นจำนวนเต็ม ได้รับ %r" % (limit,))
    if not 1 <= limit <= MAX_LIMIT:
        raise db.SchemaError(
            "limit ต้องอยู่ระหว่าง 1 ถึง %d ได้รับ %d" % (MAX_LIMIT, limit))

    if group_by is not None and isinstance(group_by, str):
        raise db.SchemaError(
            "group_by ต้องเป็นลิสต์ ไม่ใช่ข้อความ ได้รับ %r "
            "ถ้าต้องการคอลัมน์เดียวให้ส่งเป็น [%r]" % (group_by, group_by))

    groups = [db.check_dimension(g) for g in (group_by or [])]
    warnings: list[str] = []
    base, params = _filter_clauses(filters, date_from, date_to, warnings)

    # --- metric ---
    if m == "avg_days_to_close":
        select_expr = "ROUND(AVG(days_to_close), 1) AS avg_days_to_close, COUNT(*) AS n"
        base = base + ["days_to_close IS NOT NULL", "days_to_close >= %d" % MIN_DAYS]
        dropped = db.fetch("SELECT COUNT(*) AS n FROM %s WHERE days_to_close < %d"
                           % (db.TABLE, MIN_DAYS))[0]["n"]
        if dropped:
            warnings.append(
                "ตัด %d เรื่องที่ days_to_close ติดลบออกจากการคำนวณ "
                "(วันปิดเรื่องมาก่อนวันรับเรื่อง น่าจะเป็นความคลาดเคลื่อนของข้อมูลต้นทาง)" % dropped)
    else:
        select_expr = "COUNT(*) AS count"

    # --- ตัดแถวที่คอลัมน์จัดกลุ่มไม่มีค่า แล้วรายงานจำนวนที่ตัด ---
    not_null = ["%s IS NOT NULL" % g for g in groups]
    if groups:
        missing = db.fetch(
            "SELECT COUNT(*) AS n FROM %s%s" % (
                db.TABLE,
                _sql_where(base + ["(%s)" % " OR ".join("%s IS NULL" % g for g in groups)])),
            tuple(params))[0]["n"]
        if missing:
            warnings.append(
                "ตัด %d เรื่องที่ไม่ระบุ %s ออกจากผลลัพธ์ ต้องแจ้งผู้ใช้ด้วย"
                % (missing, " หรือ ".join(groups)))

    where = base + not_null
    where_sql = _sql_where(where)
    group_sql = (" GROUP BY " + ", ".join(groups)) if groups else ""

    # order_by รับได้สองแบบ
    #   ชื่อคอลัมน์ -> เรียงจากน้อยไปมาก (เช่น เรียงตามปี)
    #   ชื่อ metric -> เรียงจากมากไปน้อย (เช่น หา "อันดับหนึ่ง")
    # โมเดลมักส่งมาแบบ "count desc" หรือ "year ASC" ซึ่งเป็นภาษา SQL
    # รับไว้แล้วแยกทิศทางออกมาเอง ดีกว่าปฏิเสธแล้วให้เสียรอบ
    direction = ""
    if order_by:
        parts = str(order_by).strip().split()
        order_by = parts[0]
        if len(parts) > 1 and parts[1].upper() in ("ASC", "DESC"):
            direction = parts[1].upper()

    if order_by in ("count", "n", "share_pct", "avg_days_to_close"):
        col = "count" if order_by == "share_pct" else order_by
        ob = col + " " + (direction or "DESC")
    elif order_by:
        ob = db.check_dimension(order_by) + " " + (direction or "ASC")
    elif groups and groups[0] in ("year", "month"):
        ob = ", ".join(groups) + " ASC"     # ข้อมูลตามเวลา เรียงเก่าไปใหม่
    else:
        ob = ("avg_days_to_close" if m == "avg_days_to_close" else "count") + " DESC"

    select_cols = (", ".join(groups) + ", ") if groups else ""
    sql = "SELECT %s%s FROM %s%s%s ORDER BY %s LIMIT %d" % (
        select_cols, select_expr, db.TABLE, where_sql, group_sql, ob, int(limit))

    rows = db.fetch(sql, tuple(params))

    # --- share_pct คิดจากฐานเดียวกับที่แสดง ไม่ใช่แค่แถวที่คืนมา ---
    if m == "share_pct":
        total = db.fetch("SELECT COUNT(*) AS n FROM %s%s" % (db.TABLE, where_sql),
                         tuple(params))[0]["n"]
        for r in rows:
            r["share_pct"] = round(r["count"] * 100.0 / total, 2) if total else 0.0

    # --- ผลถูกตัดตาม limit ต้องเตือนเสมอ ไม่ว่า metric ไหน ---
    # เดิมบล็อกนี้อยู่ข้างใน if share_pct ทำให้ metric=count ซึ่งเป็นกรณีปกติที่สุด
    # ถูกตัดแบบเงียบ ๆ เช่นถามรายอำเภอได้กลับมา 100 จาก 861 โดยไม่มีใครบอก
    if groups and len(rows) >= limit:
        all_groups = db.fetch(
            "SELECT COUNT(*) AS n FROM (SELECT 1 FROM %s%s%s)" % (
                db.TABLE, where_sql, group_sql),
            tuple(params))[0]["n"]
        warnings.append(
            "ผลถูกตัดเหลือ %d กลุ่มตามค่า limit จากทั้งหมด %d กลุ่ม "
            "ถ้าคำถามต้องการครบ ให้เรียกใหม่โดยตั้ง limit อย่างน้อย %d "
            "ห้ามสรุปว่ามีเพียงเท่าที่เห็น" % (len(rows), all_groups, all_groups))

    # --- เตือนคอลัมน์ที่ใช้กรองแล้วมีค่าว่างเยอะ ---
    for col in (filters or {}):
        rate = db.null_rate(col)
        if rate > NULL_WARN_AT:
            warnings.append("คอลัมน์ %s ที่ใช้กรองมีค่าว่าง %.1f%% ของทั้งฐานข้อมูล"
                            % (col, rate * 100))

    if not rows:
        warnings.append("ไม่มีแถวที่ตรงเงื่อนไข ให้บอกผู้ใช้ตรง ๆ อย่าเดาตัวเลข")

    return {
        "rows": rows,
        "sql_used": sql,
        "params": list(params),
        "row_count": len(rows),
        "warnings": warnings,
    }


if __name__ == "__main__":
    import json

    def show(title, **kw):
        r = query_complaints(**kw)
        print("\n=== %s ===" % title)
        print("SQL :", r["sql_used"])
        for row in r["rows"][:5]:
            print("   ", json.dumps(row, ensure_ascii=False))
        for w in r["warnings"]:
            print("  เตือน:", w)

    show("5 จังหวัดที่ร้องเรียนมากสุด ปี 2021",
         group_by=["province"], filters={"year": [2021]}, limit=5)
    show("ระยะเวลาปิดเรื่องเฉลี่ยรายภูมิภาค",
         metric="avg_days_to_close", group_by=["region"], limit=4)
    show("จำนวนเรื่องรายปี", group_by=["year"], limit=4)
