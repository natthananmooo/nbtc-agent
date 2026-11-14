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

MIN_DAYS = 0          # days_to_close ติดลบ 104 แถว ตัดออกตอนคำนวณค่าเฉลี่ย
NULL_WARN_AT = 0.05   # คอลัมน์ที่ใช้กรองแล้วว่างเกินนี้ ต้องเตือน


def _filter_clauses(filters, date_from, date_to):
    """ชื่อคอลัมน์ผ่าน whitelist ค่าผ่าน ? เสมอ"""
    clauses, params = [], []
    for col, val in (filters or {}).items():
        c = db.check_dimension(col)
        vals = val if isinstance(val, (list, tuple)) else [val]
        vals = [v for v in vals if v is not None]
        if not vals:
            continue
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
    groups = [db.check_dimension(g) for g in (group_by or [])]
    base, params = _filter_clauses(filters, date_from, date_to)
    warnings: list[str] = []

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

    if order_by:
        ob = db.check_dimension(order_by) + " ASC"
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
        if groups and len(rows) >= limit:
            warnings.append("แสดงเพียง %d แถวแรก สัดส่วนคิดจากฐาน %d เรื่อง" % (limit, total))

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
