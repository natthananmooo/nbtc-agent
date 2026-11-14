"""
mcp_server/server.py - ห่อ Application Tools ทั้ง 4 ตัวให้เป็น MCP Server

ตัวนี้คือส่วน Server ของ MCP ที่แท้จริง
มันรันแยก process จาก agent และประกาศ tool ของตัวเองผ่านโปรโตคอล
ไม่ใช่การเรียกฟังก์ชัน Python ภายในโปรเซสเดียวกัน

ตรรกะทั้งหมดอยู่ใน tools/ ไฟล์นี้แค่ประกาศให้โลกภายนอกรู้จัก
ถ้าวันหนึ่งเลิกใช้ MCP ก็ import tools/ ตรง ๆ ได้โดยไม่ต้องแก้อะไร
"""

from __future__ import annotations

import os

from mcp.server.mcpserver import MCPServer

from tools.analyze import analyze_results as _analyze
from tools.chart import render_chart as _chart
from tools.query import query_complaints as _query
from tools.resolve import resolve_filter_values as _resolve
from tools.resolve import values_for_prompt as _values
from tools import db as _db

# --------------------------------------------------------------- ข้อผิดพลาด
# MCP ซ่อนรายละเอียด exception ไม่ให้ไปถึง client โมเดลจึงเห็นแค่
# "Error executing tool ..." ซึ่งแก้ตัวเองไม่ได้
# จึงดักเองแล้วคืนเป็นผลลัพธ์ธรรมดาที่บอกว่าผิดตรงไหนและต้องทำอย่างไร
# โมเดลอ่านแล้วเรียกใหม่ให้ถูกได้ในรอบถัดไป


def _recoverable(err: Exception, how_to_fix: str, **hints) -> dict:
    return {
        "ok": False,
        "error": str(err) or type(err).__name__,
        "how_to_fix": how_to_fix,
        **hints,
    }


mcp = MCPServer(
    "nbtc-complaints",
    instructions="สถิติเรื่องร้องเรียนในกิจการโทรคมนาคม จาก กสทช. "
                 "42,806 เรื่อง ปี 2552 ถึง 2569 ฐานข้อมูลเปิดแบบอ่านอย่างเดียว",
)


@mcp.tool()
def resolve_filter_values(user_phrase: str, field: str | None = None) -> dict:
    """แปลงคำที่ผู้ใช้พิมพ์ ให้เป็นค่าที่มีอยู่จริงในฐานข้อมูล สำหรับคอลัมน์ที่เป็นข้อความเท่านั้น

    ห้ามเรียกกับ year หรือ month เพราะเป็นตัวเลข ไม่ใช่ข้อความ
    ปีและเดือนส่งเข้า filters ของ query_complaints ได้ตรง ๆ เลย
    เช่น ผู้ใช้ถามถึงปี 2564 ให้ข้ามมาเรียก query_complaints ด้วย
    filters={"year": [2564]} ทันที ไม่ต้องแปลงอะไรก่อน

    เรียกตัวนี้ก่อนเสมอ เมื่อคำถามอ้างถึงผู้ให้บริการ ประเภทบริการ กลุ่มปัญหา
    หรือพื้นที่ เพราะคำที่คนพูดมักไม่ตรงกับค่าที่เก็บจริง เช่น ผู้ใช้พิมพ์ AIS
    แต่ฐานข้อมูลเก็บว่า บริษัท แอดวานซ์ ไวร์เลส เน็ทเวอร์ค จำกัด

    ดูค่า confidence ที่คืนมาแล้วทำตามนี้
      high      ใช้ค่าที่ได้ และบอกผู้ใช้ว่าใช้ค่าอะไร
      ambiguous ห้ามเดา ให้ถามผู้ใช้ว่าหมายถึงตัวเลือกไหน พร้อมบอกจำนวนเรื่องของแต่ละตัว
      none      ไม่มีค่านี้ในฐานข้อมูล ให้ถามผู้ใช้ใหม่ หรือบอกว่าไม่มีข้อมูล

    Args:
        user_phrase: คำที่ผู้ใช้พิมพ์ เช่น AIS, มือถือ, ภาคเหนือ
        field: ระบุคอลัมน์ถ้ารู้แน่ เช่น provider, service_type, problem_group,
               region, province, district, channel, status
    """
    try:
        return _resolve(user_phrase, field)
    except Exception as e:
        return _recoverable(
            e, "ตรวจชื่อ field ให้ตรงกับรายการที่ให้ไว้ หรือเว้นว่างเพื่อค้นทุกคอลัมน์",
            valid_fields=list(_db.DIMENSIONS))


@mcp.tool()
def query_complaints(
    metric: str = "count",
    group_by: list[str] | None = None,
    filters: dict | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    order_by: str | None = None,
    limit: int = 100,
) -> dict:
    """สืบค้นสถิติเรื่องร้องเรียนโทรคมนาคม แบบอ่านอย่างเดียว

    เครื่องมือนี้ประกอบ SQL ให้เอง ห้ามส่ง SQL เข้ามา
    ค่าที่ใช้ใน filters ต้องเป็นค่าจริงที่ได้จาก resolve_filter_values เท่านั้น

    สิ่งที่คืนกลับ
      rows      ผลลัพธ์
      sql_used  SQL ที่รันจริง ต้องอ้างอิงเป็นแหล่งที่มาของตัวเลขในคำตอบ
      warnings  ข้อจำกัดของผลลัพธ์ ต้องแจ้งผู้ใช้ทุกข้อ ห้ามข้าม

    Args:
        metric: count, avg_days_to_close หรือ share_pct
        group_by: คอลัมน์ที่จัดกลุ่ม เช่น ["year"], ["province"], ["provider"]
        filters: เช่น {"year": [2021], "service_type": ["อินเทอร์เน็ต"]}
        date_from: วันเริ่ม รูปแบบ YYYY-MM-DD กรองจากวันรับเรื่อง
        date_to: วันสิ้นสุด รูปแบบ YYYY-MM-DD
        order_by: ใส่ชื่อคอลัมน์เพื่อเรียงจากน้อยไปมาก เช่น year
                  หรือใส่ชื่อ metric เช่น count เพื่อเรียงจากมากไปน้อย
                  สำคัญ: ถ้าคำถามถามว่า "ปีไหนมากที่สุด" ต้องใส่ order_by=count
                  เพราะถ้าไม่ใส่ ระบบจะเรียงตามปีจากเก่าไปใหม่
        limit: จำนวนแถวสูงสุด
    """
    try:
        return _query(metric, group_by, filters, date_from, date_to, order_by, limit)
    except _db.SchemaError as e:
        return _recoverable(
            e, "เลือกชื่อคอลัมน์หรือ metric จากรายการข้างล่าง แล้วเรียกใหม่",
            valid_columns=list(_db.DIMENSIONS), valid_metrics=list(_db.MEASURES))
    except Exception as e:
        return _recoverable(
            e, "ตรวจรูปแบบพารามิเตอร์ เช่น group_by ต้องเป็นลิสต์ของชื่อคอลัมน์ "
               "filters ต้องเป็น dict ชื่อคอลัมน์ไปหาลิสต์ของค่า "
               "date_from และ date_to ต้องเป็น YYYY-MM-DD โดยปีเป็น พ.ศ.",
            valid_columns=list(_db.DIMENSIONS), valid_metrics=list(_db.MEASURES))


@mcp.tool()
def analyze_results(
    rows: list[dict],
    question: str = "",
    mode: str = "summary",
    warnings: list[str] | None = None,
) -> dict:
    """สรุปตัวเลขจากผลลัพธ์ของ query_complaints ด้วยการคำนวณ ไม่ใช่การประมาณ

    ส่ง warnings ที่ได้จาก query_complaints เข้ามาด้วย เพื่อให้ caveats ครบ

    Args:
        rows: ผลลัพธ์จาก query_complaints
        question: คำถามเดิมของผู้ใช้
        mode: summary, trend (ใช้กับข้อมูลรายปีรายเดือน), compare หรือ share
        warnings: warnings ที่ query_complaints คืนมา
    """
    try:
        return _analyze(rows, question, mode, warnings)
    except Exception as e:
        return _recoverable(
            e, "ส่ง rows ที่ได้จาก query_complaints มาตรง ๆ และ mode ต้องเป็นหนึ่งใน "
               "summary, trend, compare, share",
            valid_modes=["summary", "trend", "compare", "share"])


@mcp.tool()
def render_chart(
    rows: list[dict],
    chart_type: str,
    x: str,
    y: str,
    title: str,
    filename: str | None = None,
) -> dict:
    """วาดกราฟจากผลลัพธ์ แล้วคืน path ของไฟล์ภาพ

    เรียกเมื่อผู้ใช้ขอกราฟเท่านั้น ถ้าไม่ได้ขอก็ตอบเป็นข้อความพอ

    Args:
        rows: ผลลัพธ์จาก query_complaints
        chart_type: bar, barh, line หรือ pie
        x: ชื่อคอลัมน์หมวดหมู่ เช่น province
        y: ชื่อคอลัมน์ตัวเลข เช่น count
        title: หัวกราฟ
        filename: ตั้งชื่อไฟล์เองได้ ถ้าไม่ระบุจะตั้งให้
    """
    try:
        return _chart(rows, chart_type, x, y, title, filename)
    except Exception as e:
        cols = list(rows[0]) if rows else []
        return _recoverable(
            e, "x และ y ต้องเป็นชื่อคอลัมน์ที่มีอยู่จริงใน rows "
               "และ chart_type ต้องเป็นหนึ่งใน bar, barh, line, pie",
            columns_in_rows=cols, valid_chart_types=["bar", "barh", "line", "pie"])


# ---------------------------------------------------------------- resource
# รายการค่าที่มีจริงในฐานข้อมูล ส่งให้ agent เอาไปใส่ system prompt
# เป็น resource ไม่ใช่ tool เพราะเป็นข้อมูลบริบท ไม่ใช่การกระทำ
# agent จึงไม่ต้องเข้าถึงไฟล์ฐานข้อมูลเองเลย ซึ่งเป็นจุดสำคัญด้านความปลอดภัย
@mcp.resource("schema://values", mime_type="text/plain")
def schema_values() -> str:
    """รายการค่าไม่ซ้ำของแต่ละคอลัมน์ และคำอธิบายโครงสร้างตาราง"""
    years = _db.fetch("SELECT MIN(year) a, MAX(year) b FROM complaints WHERE year IS NOT NULL")[0]
    per_year = _db.fetch(
        "SELECT year, COUNT(*) n FROM complaints WHERE year IS NOT NULL GROUP BY year ORDER BY year")

    lines = [
        "ตาราง complaints มี %d แถว" % _db.total_rows(),
        "",
        "ปีที่มีข้อมูล เก็บเป็น พ.ศ. ตามที่ กสทช. เผยแพร่ ตั้งแต่ %d ถึง %d"
        % (years["a"], years["b"]),
        "  " + "  ".join("%d:%d" % (r["year"], r["n"]) for r in per_year),
        "ผู้ใช้พิมพ์ พ.ศ. มาได้เลย ตรงกับที่เก็บ ถ้าพิมพ์ ค.ศ. มา query_complaints แปลงขึ้นให้",
        "วันที่เก็บรูปแบบ ปปปป-ดด-วว โดยปีเป็น พ.ศ. เช่น 2552-01-05",
        "คอลัมน์ received_date_raw และ closed_date_raw เก็บสตริงต้นฉบับไว้ครบ",
        "เดือนเก็บเป็นตัวเลข 1 ถึง 12",
        "",
        "คอลัมน์ที่กรองและจัดกลุ่มได้",
    ]
    for col, desc in _db.DIMENSIONS.items():
        lines.append("  %-14s %s" % (col, desc))
    lines += ["", "ค่าที่วัดได้ (metric)"]
    for m, desc in _db.MEASURES.items():
        lines.append("  %-18s %s" % (m, desc))
    lines += ["", "ค่าที่มีอยู่จริงในแต่ละคอลัมน์", _values()]
    return "\n".join(lines)


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="streamable-http",
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )
