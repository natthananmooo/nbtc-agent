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

mcp = MCPServer(
    "nbtc-complaints",
    instructions="สถิติเรื่องร้องเรียนในกิจการโทรคมนาคม จาก กสทช. "
                 "42,806 เรื่อง ปี 2552 ถึง 2569 ฐานข้อมูลเปิดแบบอ่านอย่างเดียว",
)


@mcp.tool()
def resolve_filter_values(user_phrase: str, field: str | None = None) -> dict:
    """แปลงคำที่ผู้ใช้พิมพ์ ให้เป็นค่าที่มีอยู่จริงในฐานข้อมูล

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
    return _resolve(user_phrase, field)


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
        order_by: เรียงตามคอลัมน์นี้จากน้อยไปมาก ถ้าไม่ระบุจะเรียงตามค่าที่วัดจากมากไปน้อย
        limit: จำนวนแถวสูงสุด
    """
    return _query(metric, group_by, filters, date_from, date_to, order_by, limit)


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
    return _analyze(rows, question, mode, warnings)


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
    return _chart(rows, chart_type, x, y, title, filename)


# ---------------------------------------------------------------- resource
# รายการค่าที่มีจริงในฐานข้อมูล ส่งให้ agent เอาไปใส่ system prompt
# เป็น resource ไม่ใช่ tool เพราะเป็นข้อมูลบริบท ไม่ใช่การกระทำ
# agent จึงไม่ต้องเข้าถึงไฟล์ฐานข้อมูลเองเลย ซึ่งเป็นจุดสำคัญด้านความปลอดภัย
@mcp.resource("schema://values", mime_type="text/plain")
def schema_values() -> str:
    """รายการค่าไม่ซ้ำของแต่ละคอลัมน์ และคำอธิบายโครงสร้างตาราง"""
    lines = ["ตาราง complaints มี %d แถว" % _db.total_rows(), "", "คอลัมน์ที่กรองและจัดกลุ่มได้"]
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
