"""
mcp_server/server.py - ห่อ Application Tools ทั้ง 4 ตัวให้เป็น MCP Server

ตัวนี้คือส่วน Server ของ MCP ที่แท้จริง
มันรันแยก process จาก agent และประกาศ tool ของตัวเองผ่านโปรโตคอล
ไม่ใช่การเรียกฟังก์ชัน Python ภายในโปรเซสเดียวกัน

ตรรกะทั้งหมดอยู่ใน tools/ ไฟล์นี้แค่ประกาศให้โลกภายนอกรู้จัก
ถ้าวันหนึ่งเลิกใช้ MCP ก็ import tools/ ตรง ๆ ได้โดยไม่ต้องแก้อะไร
"""

from __future__ import annotations

import json
import os
import sys
import time

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


# ------------------------------------------------------------------- log
# พิมพ์ว่าใครเรียก tool อะไร ด้วยพารามิเตอร์ไหน และได้อะไรกลับ
# เพื่อให้ docker compose logs -f mcp อ่านแล้วเห็นการทำงานจริง
LOG = os.environ.get("MCP_LOG", "1") != "0"


def _brief(v, limit=90) -> str:
    t = json.dumps(v, ensure_ascii=False, default=str)
    return t if len(t) <= limit else t[:limit] + "…"


def _log_call(name: str, args: dict, result, started: float):
    if not LOG:
        return
    ms = (time.time() - started) * 1000
    shown = {k: v for k, v in args.items() if v not in (None, [], {})}
    line = "[tool] %-22s %s" % (name, _brief(shown, 120))

    if isinstance(result, dict):
        if result.get("ok") is False:
            line += "\n        ผิดพลาด  %s" % result.get("error", "")[:110]
        else:
            if "sql_used" in result:
                line += "\n        SQL      %s" % result["sql_used"][:110]
                line += "\n        ได้      %d แถว" % result.get("row_count", 0)
            if "confidence" in result:
                line += "\n        ได้      confidence=%s  %d ตัวเลือก" % (
                    result["confidence"], len(result.get("candidates", [])))
            if "findings" in result:
                line += "\n        ได้      %d ข้อสรุป" % len(result["findings"])
            if "image_path" in result:
                line += "\n        ได้      %s" % result.get("image_path")
            for w in (result.get("warnings") or [])[:3]:
                line += "\n        เตือน    %s" % w[:110]

    print("%s  %.0f ms" % (line, ms), file=sys.stderr, flush=True)


# ------------------------------------------------------- ผลลัพธ์ที่อ้างถึงได้
# เดิม analyze_results กับ render_chart ต้องให้โมเดลก๊อป rows ทั้งก้อนส่งกลับมา
# ซึ่งพลาดง่ายมาก ของจริงพบว่าโมเดลส่งสตริง "{%call_result%...}" มาแทนข้อมูล
# จึงเก็บผลไว้ฝั่ง server แล้วให้อ้างด้วย result_id สั้น ๆ แทน
_RESULTS: dict[str, dict] = {}
_RESULT_ORDER: list[str] = []
_KEEP = 20


def _remember(payload: dict) -> str:
    rid = "r%d" % (len(_RESULT_ORDER) + 1)
    _RESULTS[rid] = payload
    _RESULT_ORDER.append(rid)
    while len(_RESULT_ORDER) > _KEEP:
        _RESULTS.pop(_RESULT_ORDER.pop(0), None)
    return rid


def _rows_from(result_id, rows):
    """คืน (rows, warnings, ข้อความผิดพลาด)"""
    if result_id:
        got = _RESULTS.get(result_id)
        if got is None:
            return None, None, ("ไม่พบ result_id %r ที่มีอยู่คือ %s"
                                % (result_id, ", ".join(_RESULT_ORDER) or "ยังไม่มี"))
        return got["rows"], got.get("warnings", []), None
    if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):
        return rows, None, None
    return None, None, ("rows ต้องเป็นลิสต์ของ object ที่ได้จาก query_complaints "
                        "ห้ามส่งข้อความอ้างอิงอย่าง {%call_result%...} "
                        "วิธีที่ง่ายกว่าคือใส่ result_id ที่ query_complaints คืนมา")


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

    ผลลัพธ์มีฟิลด์ use_in_filters ที่พร้อมใช้ ให้ก๊อปไปใส่ filters ของ query_complaints
    ทั้งก้อน ห้ามหยิบไปแค่ค่าเดียว เช่น AIS มี 4 บริษัทในเครือ ต้องใส่ครบทั้ง 4
    ถ้าใส่ไม่ครบ ตัวเลขจะผิดและห้ามอ้างว่ารวมบริษัทที่ไม่ได้ใส่

    ดูค่า confidence ที่คืนมาแล้วทำตามนี้
      high      ใช้ use_in_filters ที่ให้มา และบอกผู้ใช้ว่าใช้ค่าอะไรบ้าง
      ambiguous ห้ามเดา ให้ถามผู้ใช้ว่าหมายถึงตัวเลือกไหน พร้อมบอกจำนวนเรื่องของแต่ละตัว
      none      ไม่มีค่านี้ในฐานข้อมูล ให้ถามผู้ใช้ใหม่ หรือบอกว่าไม่มีข้อมูล

    Args:
        user_phrase: คำที่ผู้ใช้พิมพ์ เช่น AIS, มือถือ, ภาคเหนือ
        field: ระบุคอลัมน์ถ้ารู้แน่ เช่น provider, service_type, problem_group,
               region, province, district, channel, status
    """
    t0 = time.time()
    try:
        out = _resolve(user_phrase, field)
    except Exception as e:
        out = _recoverable(
            e, "ตรวจชื่อ field ให้ตรงกับรายการที่ให้ไว้ หรือเว้นว่างเพื่อค้นทุกคอลัมน์",
            valid_fields=list(_db.DIMENSIONS))
    _log_call("resolve_filter_values",
              {"user_phrase": user_phrase, "field": field}, out, t0)
    return out


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
    t0 = time.time()
    try:
        out = _query(metric, group_by, filters, date_from, date_to, order_by, limit)
    except _db.SchemaError as e:
        out = _recoverable(
            e, "เลือกชื่อคอลัมน์หรือ metric จากรายการข้างล่าง แล้วเรียกใหม่",
            valid_columns=list(_db.DIMENSIONS), valid_metrics=list(_db.MEASURES))
    except Exception as e:
        out = _recoverable(
            e, "ตรวจรูปแบบพารามิเตอร์ เช่น group_by ต้องเป็นลิสต์ของชื่อคอลัมน์ "
               "filters ต้องเป็น dict ชื่อคอลัมน์ไปหาลิสต์ของค่า "
               "date_from และ date_to ต้องเป็น YYYY-MM-DD โดยปีเป็น พ.ศ.",
            valid_columns=list(_db.DIMENSIONS), valid_metrics=list(_db.MEASURES))
    if isinstance(out, dict) and out.get("ok") is not False:
        out["result_id"] = _remember({"rows": out.get("rows", []),
                                      "warnings": out.get("warnings", [])})
    _log_call("query_complaints",
              {"metric": metric, "group_by": group_by, "filters": filters,
               "date_from": date_from, "date_to": date_to,
               "order_by": order_by, "limit": limit}, out, t0)
    return out


@mcp.tool()
def analyze_results(
    result_id: str | None = None,
    rows: list[dict] | None = None,
    question: str = "",
    mode: str = "summary",
) -> dict:
    """สรุปตัวเลขจากผลลัพธ์ของ query_complaints ด้วยการคำนวณ ไม่ใช่การประมาณ

    วิธีที่ถูกต้องคือใส่ result_id ที่ query_complaints คืนมา
    ไม่ต้องก๊อป rows กลับมาเอง และ warnings จะถูกดึงมาให้อัตโนมัติ

    Args:
        result_id: รหัสผลลัพธ์จาก query_complaints เช่น r1
        rows: ใส่แทน result_id ได้ ต้องเป็นลิสต์ของ object จริงเท่านั้น
        question: คำถามเดิมของผู้ใช้
        mode: summary, trend (ใช้กับข้อมูลรายปีรายเดือน), compare หรือ share
    """
    t0 = time.time()
    data, warns, err = _rows_from(result_id, rows)
    if err:
        out = {"ok": False, "error": err,
               "how_to_fix": "เรียก query_complaints ก่อน แล้วส่ง result_id ที่ได้มาที่นี่",
               "available_result_ids": list(_RESULT_ORDER)}
    else:
        try:
            out = _analyze(data, question, mode, warns)
        except Exception as e:
            out = _recoverable(
                e, "mode ต้องเป็นหนึ่งใน summary, trend, compare, share",
                valid_modes=["summary", "trend", "compare", "share"])
    _log_call("analyze_results",
              {"result_id": result_id, "mode": mode, "rows": len(data or [])}, out, t0)
    return out


@mcp.tool()
def render_chart(
    chart_type: str,
    x: str,
    y: str,
    title: str,
    result_id: str | None = None,
    rows: list[dict] | None = None,
    filename: str | None = None,
) -> dict:
    """วาดกราฟจากผลลัพธ์ แล้วคืน path ของไฟล์ภาพ

    เรียกเมื่อผู้ใช้ขอกราฟเท่านั้น ถ้าไม่ได้ขอก็ตอบเป็นข้อความพอ
    ใส่ result_id ที่ query_complaints คืนมา ไม่ต้องก๊อป rows

    Args:
        chart_type: bar, barh, line หรือ pie (ไม่มีสามมิติ)
        x: ชื่อคอลัมน์หมวดหมู่ เช่น province
        y: ชื่อคอลัมน์ตัวเลข เช่น count
        title: หัวกราฟ
        result_id: รหัสผลลัพธ์จาก query_complaints เช่น r1
        rows: ใส่แทน result_id ได้ ต้องเป็นลิสต์ของ object จริงเท่านั้น
        filename: ตั้งชื่อไฟล์เองได้
    """
    t0 = time.time()
    data, _, err = _rows_from(result_id, rows)
    if err:
        out = {"ok": False, "error": err,
               "how_to_fix": "เรียก query_complaints ก่อน แล้วส่ง result_id ที่ได้มาที่นี่",
               "available_result_ids": list(_RESULT_ORDER)}
    else:
        try:
            out = _chart(data, chart_type, x, y, title, filename)
        except Exception as e:
            out = _recoverable(
                e, "x และ y ต้องเป็นชื่อคอลัมน์ที่มีอยู่จริงใน rows "
                   "และ chart_type ต้องเป็นหนึ่งใน bar, barh, line, pie",
                columns_in_rows=list(data[0]) if data else [],
                valid_chart_types=["bar", "barh", "line", "pie"])
    _log_call("render_chart",
              {"result_id": result_id, "chart_type": chart_type, "x": x, "y": y}, out, t0)
    return out


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
