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
import traceback

from mcp.server.mcpserver import MCPServer

from tools.analyze import analyze_results as _analyze
from tools.chart import render_chart as _chart
from tools.dashboard import generate_dashboard as _dashboard
from tools.graph import explore_relations as _relations
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
            if "related" in result:
                line += "\n        ได้      ความสัมพันธ์ %s" % ", ".join(
                    "%s %d ค่า" % (k, len(v)) for k, v in result["related"].items())
            if "similar" in result:
                line += "\n        ได้      ค่าใกล้เคียง %d ตัว เทียบบน %s" % (
                    len(result["similar"]), result.get("compared_on"))
            if "findings" in result:
                line += "\n        ได้      %d ข้อสรุป" % len(result["findings"])
            if "image_path" in result:
                line += "\n        ได้      %s" % result.get("image_path")
            if "html_path" in result:
                line += "\n        ได้      %s (%d แผง)" % (
                    result.get("html_path"), result.get("panels_drawn", 0))
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


# ความผิดพลาดที่คาดไว้แล้วและ LLM แก้ต่อเองได้ เช่นส่งชื่อคอลัมน์ผิด
# ต้องแยกจากบั๊กของเราเอง ไม่งั้น KeyError ที่เกิดจากโค้ดเราเขียนผิด
# จะถูกห่อเป็นคำแนะนำส่งให้ LLM แล้ว LLM ก็ลองใหม่วนไป โดยไม่มีใครเห็นว่ามีบั๊ก
EXPECTED = (_db.SchemaError, ValueError)


def _recoverable(err: Exception, how_to_fix: str, code: str = "INVALID_ARG",
                 **hints) -> dict:
    """ระดับที่หนึ่ง ผิดแล้วแก้ต่อได้ คืนเป็นผลลัพธ์ปกติให้ LLM อ่านแล้วลองใหม่"""
    return {
        "ok": False,
        "code": code,
        "error": str(err) or type(err).__name__,
        "how_to_fix": how_to_fix,
        **hints,
    }


def _unexpected(name: str, err: Exception):
    """ระดับที่สอง ไม่คาดคิด แปลว่าเป็นบั๊กของเรา ต้องบันทึกแล้วโยนต่อให้เห็น"""
    print("[bug] %s ล้มด้วย %s: %s" % (name, type(err).__name__, err),
          file=sys.stderr, flush=True)
    traceback.print_exc()
    raise err


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

    ฟิลด์ rows_all_years ในแต่ละตัวเลือก คือยอดรวมทุกปีของค่านั้นในฐานข้อมูล
    มีไว้ให้ผู้ใช้เทียบขนาดตอนถามกลับเท่านั้น ไม่ใช่คำตอบของคำถาม
    ห้ามเอามาบวกกันแล้วตอบ ต้องเรียก query_complaints เสมอ
      none      ไม่มีค่าที่ตรง ถ้าระบุ field มาจะได้ field_values กลับไปด้วย
                เป็นรายการค่าจริงของคอลัมน์นั้นพร้อมจำนวนเรื่อง
                ให้เสนอเฉพาะค่าจากรายการนั้น ห้ามเสนอค่าจากคอลัมน์อื่น
                ถ้าไม่ได้ระบุ field ให้เรียกใหม่โดยระบุคอลัมน์ที่น่าจะใช่

    เมื่อผิดพลาดแบบแก้ต่อได้ จะคืน ok เป็น false พร้อม code, error และ how_to_fix
    ให้อ่าน how_to_fix แล้วเรียกใหม่ให้ถูก ห้ามเดาคำตอบแทน

    Args:
        user_phrase: คำที่ผู้ใช้พิมพ์ เช่น AIS, มือถือ, ภาคเหนือ
        field: ระบุคอลัมน์ถ้ารู้แน่ เช่น provider, service_type, problem_group,
               region, province, district, channel, status
    """
    t0 = time.time()
    try:
        out = _resolve(user_phrase, field)
    except EXPECTED as e:
        out = _recoverable(
            e, "ตรวจชื่อ field ให้ตรงกับรายการที่ให้ไว้ หรือเว้นว่างเพื่อค้นทุกคอลัมน์",
            valid_fields=list(_db.DIMENSIONS))
    except Exception as e:
        _unexpected("resolve_filter_values", e)
    _log_call("resolve_filter_values",
              {"user_phrase": user_phrase, "field": field}, out, t0)
    return out


@mcp.tool()
def query_complaints(
    metric: str = "count",
    group_by: list[str] | str | None = None,
    filters: dict | str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    order_by: str | None = None,
    limit: int = 100,
) -> dict:
    """สืบค้นสถิติเรื่องร้องเรียนโทรคมนาคม แบบอ่านอย่างเดียว

    ห้ามใช้เมื่อยังไม่รู้ว่าค่าที่จะกรองคืออะไร ให้เรียก resolve_filter_values ก่อน
    แล้วเอา use_in_filters ที่ได้มาใส่ ไม่งั้นจะได้ SQL ที่ถูกแต่ตอบคนละคำถาม

    เครื่องมือนี้ประกอบ SQL ให้เอง ห้ามส่ง SQL เข้ามา
    ค่าที่ใช้ใน filters ต้องเป็นค่าจริงที่ได้จาก resolve_filter_values เท่านั้น

    สิ่งที่คืนกลับ
      rows      ผลลัพธ์
      sql_used  SQL ที่รันจริง ต้องอ้างอิงเป็นแหล่งที่มาของตัวเลขในคำตอบ
      warnings  ข้อจำกัดของผลลัพธ์ ต้องแจ้งผู้ใช้ทุกข้อ ห้ามข้าม

    เมื่อผิดพลาดแบบแก้ต่อได้ จะคืน ok เป็น false พร้อม code, error และ how_to_fix
    ให้อ่าน how_to_fix แล้วเรียกใหม่ให้ถูก ห้ามเดาคำตอบแทน

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
    except EXPECTED as e:
        out = _recoverable(
            e, "ตรวจรูปแบบพารามิเตอร์ เช่น group_by ต้องเป็นลิสต์ของชื่อคอลัมน์ "
               "filters ต้องเป็น dict ชื่อคอลัมน์ไปหาลิสต์ของค่า "
               "date_from และ date_to ต้องเป็น YYYY-MM-DD โดยปีเป็น พ.ศ.",
            valid_columns=list(_db.DIMENSIONS), valid_metrics=list(_db.MEASURES))
    except Exception as e:
        _unexpected("query_complaints", e)
    if isinstance(out, dict) and out.get("ok") is not False:
        out["result_id"] = _remember({"rows": out.get("rows", []),
                                      "warnings": out.get("warnings", []),
                                      "sql": out.get("sql_used", "")})
    _log_call("query_complaints",
              {"metric": metric, "group_by": group_by, "filters": filters,
               "date_from": date_from, "date_to": date_to,
               "order_by": order_by, "limit": limit}, out, t0)
    return out


@mcp.tool()
def explore_relations(
    value: str,
    field: str | None = None,
    to_field: str | None = None,
    mode: str = "related",
    top_k: int = 8,
) -> dict:
    """ดูว่าค่าหนึ่งเกี่ยวข้องกับอะไรในข้อมูลจริง ใช้ตอนยังไม่รู้ว่าควรกรองด้วยอะไร

    ห้ามใช้เมื่อคำถามระบุชัดแล้วว่าจะนับอะไร ให้ไป query_complaints ตรง ๆ
    ห้ามใช้ตัวเลขที่ได้จากตัวนี้ไปตอบผู้ใช้ เพราะเป็นยอดรวมทุกปี

    ตัวนี้ไม่ได้ตอบคำถามแทน query_complaints แต่ช่วยหาทิศทางก่อนจะไปนับจริง
    เรียกเมื่อคำถามเป็นแนวสำรวจความสัมพันธ์ เช่น
      เจ้านี้มีปัญหาอะไรเป็นพิเศษ
      ปัญหาแบบนี้มักเกิดกับบริการประเภทไหน
      มีเจ้าไหนลักษณะปัญหาคล้ายเจ้านี้บ้าง
    ถ้าคำถามระบุสิ่งที่จะนับชัดอยู่แล้ว ให้ไป query_complaints ตรง ๆ ไม่ต้องเรียกตัวนี้

    ค่าที่ส่งเข้ามาต้องเป็นค่าจริงในฐานข้อมูล จึงต้องเรียก resolve_filter_values ก่อน
    แล้วเอาค่าที่ได้มาส่งทั้งสตริง ห้ามพิมพ์ย่อเอง

    ผลลัพธ์แต่ละรายการมีสามตัวเลข
      rows_all_years จำนวนเรื่องรวมทุกปีที่ปรากฏร่วมกัน
      share_pct      สัดส่วนภายในค่าที่ถาม
      lift           เทียบกับค่าเฉลี่ยทั้งระบบ มากกว่า 1 คือเด่นผิดปกติ
                     ตัวนี้สำคัญกว่าจำนวนดิบ เพราะเจ้าใหญ่ย่อมเยอะไปหมดทุกหมวด

    ตัวเลขทั้งหมดเป็นยอดรวมทุกปี ห้ามเอาไปตอบเมื่อผู้ใช้ถามตัวเลขหรือระบุปี
    ให้ใช้เป็นตัวช่วยเลือกว่าจะกรองด้วยอะไร แล้วเรียก query_complaints ต่อเสมอ

    เมื่อผิดพลาดแบบแก้ต่อได้ จะคืน ok เป็น false พร้อม code, error และ how_to_fix
    ให้อ่าน how_to_fix แล้วเรียกใหม่ให้ถูก ห้ามเดาคำตอบแทน

    Args:
        value: ค่าจริงในฐานข้อมูล เช่น บริษัท ทรู มูฟ เอช ยูนิเวอร์แซล คอมมิวนิเคชั่น จำกัด
        field: คอลัมน์ของค่านั้น เช่น provider, service_type, problem_group, province
        to_field: อยากดูความสัมพันธ์ไปยังคอลัมน์ไหน เว้นว่างได้จะคืนทุกคอลัมน์ที่เชื่อมถึง
        mode: related คือสิ่งที่พบร่วมกันบ่อย, similar คือค่าอื่นที่มีรูปแบบปัญหาคล้ายกัน
        top_k: จำนวนรายการสูงสุดต่อคอลัมน์
    """
    t0 = time.time()
    try:
        out = _relations(value, field, to_field, mode, top_k)
    except EXPECTED as e:
        out = _recoverable(
            e, "ตรวจว่า value เป็นค่าจริงที่ได้จาก resolve_filter_values "
               "และ mode เป็น related หรือ similar เท่านั้น",
            valid_modes=["related", "similar"],
            valid_fields=list(_db.DIMENSIONS))
    except Exception as e:
        _unexpected("explore_relations", e)
    _log_call("explore_relations",
              {"value": value, "field": field, "to_field": to_field, "mode": mode}, out, t0)
    return out


@mcp.tool()
def analyze_results(
    result_id: str | None = None,
    rows: list[dict] | None = None,
    question: str = "",
    mode: str = "summary",
) -> dict:
    """สรุปตัวเลขจากผลลัพธ์ของ query_complaints ด้วยการคำนวณ ไม่ใช่การประมาณ

    ห้ามใช้ตอนที่ยังไม่มี result_id ต้องเรียก query_complaints ให้ได้ข้อมูลมาก่อน
    ห้ามใช้เพื่อหาข้อมูลใหม่ ตัวนี้คำนวณจากแถวที่ดึงมาแล้วเท่านั้น

    วิธีที่ถูกต้องคือใส่ result_id ที่ query_complaints คืนมา
    ไม่ต้องก๊อป rows กลับมาเอง และ warnings จะถูกดึงมาให้อัตโนมัติ

    เมื่อผิดพลาดแบบแก้ต่อได้ จะคืน ok เป็น false พร้อม code, error และ how_to_fix
    ให้อ่าน how_to_fix แล้วเรียกใหม่ให้ถูก ห้ามเดาคำตอบแทน

    Args:
        result_id: รหัสผลลัพธ์จาก query_complaints เช่น r1
        rows: ใส่แทน result_id ได้ ต้องเป็นลิสต์ของ object จริงเท่านั้น
        question: คำถามเดิมของผู้ใช้
        mode: summary, trend (ใช้กับข้อมูลรายปีรายเดือน), compare หรือ share
    """
    t0 = time.time()
    data, warns, err = _rows_from(result_id, rows)
    if err:
        out = {"ok": False, "code": "RESULT_NOT_FOUND", "error": err,
               "how_to_fix": "เรียก query_complaints ก่อน แล้วส่ง result_id ที่ได้มาที่นี่",
               "available_result_ids": list(_RESULT_ORDER)}
    else:
        try:
            out = _analyze(data, question, mode, warns)
        except EXPECTED as e:
            out = _recoverable(
                e, "mode ต้องเป็นหนึ่งใน summary, trend, compare, share",
                valid_modes=["summary", "trend", "compare", "share"])
        except Exception as e:
            _unexpected("analyze_results", e)
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

    ห้ามใช้เมื่อผู้ใช้ขอภาพรวมหรือหลายมุมมองพร้อมกัน ให้ใช้ generate_dashboard แทน
    ห้ามใช้เมื่อผู้ใช้ไม่ได้ขอกราฟ การวาดทิ้งไว้เปลืองเวลาโดยไม่ช่วยอะไร

    เรียกเมื่อผู้ใช้ขอกราฟเท่านั้น ถ้าไม่ได้ขอก็ตอบเป็นข้อความพอ
    ใส่ result_id ที่ query_complaints คืนมา ไม่ต้องก๊อป rows

    เมื่อผิดพลาดแบบแก้ต่อได้ จะคืน ok เป็น false พร้อม code, error และ how_to_fix
    ให้อ่าน how_to_fix แล้วเรียกใหม่ให้ถูก ห้ามเดาคำตอบแทน

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
        out = {"ok": False, "code": "RESULT_NOT_FOUND", "error": err,
               "how_to_fix": "เรียก query_complaints ก่อน แล้วส่ง result_id ที่ได้มาที่นี่",
               "available_result_ids": list(_RESULT_ORDER)}
    else:
        try:
            out = _chart(data, chart_type, x, y, title, filename)
        except EXPECTED as e:
            out = _recoverable(
                e, "x และ y ต้องเป็นชื่อคอลัมน์ที่มีอยู่จริงใน rows "
                   "และ chart_type ต้องเป็นหนึ่งใน bar, barh, line, pie",
                columns_in_rows=list(data[0]) if data else [],
                valid_chart_types=["bar", "barh", "line", "pie"])
        except Exception as e:
            _unexpected("render_chart", e)
    _log_call("render_chart",
              {"result_id": result_id, "chart_type": chart_type, "x": x, "y": y}, out, t0)
    return out


@mcp.tool()
def generate_dashboard(
    title: str,
    panels: list[dict],
    notes: list[str] | None = None,
) -> dict:
    """รวมหลายกราฟเป็นหน้าสรุปเดียว ได้ไฟล์ HTML ที่ฝังรูปไว้ในตัว

    เรียกเมื่อผู้ใช้ขอภาพรวม หน้าสรุป หรือ dashboard ที่มีหลายมุมมอง
    ถ้าผู้ใช้ขอกราฟอันเดียว ใช้ render_chart พอ

    ต้องเรียก query_complaints ให้ครบทุกมุมมองก่อน แล้วเอา result_id มาใส่
    SQL ของแต่ละแผงจะถูกแสดงใต้กราฟอัตโนมัติ ผู้อ่านตรวจที่มาได้

    เมื่อผิดพลาดแบบแก้ต่อได้ จะคืน ok เป็น false พร้อม code, error และ how_to_fix
    ให้อ่าน how_to_fix แล้วเรียกใหม่ให้ถูก ห้ามเดาคำตอบแทน

    Args:
        title: หัวข้อหน้าสรุป
        panels: รายการแผง แต่ละแผงเป็น object ที่มี
                result_id ที่ได้จาก query_complaints
                chart_type เป็น bar, barh, line หรือ pie
                x ชื่อคอลัมน์หมวดหมู่ เช่น year หรือ province
                y ชื่อคอลัมน์ตัวเลข เช่น count
                title หัวข้อของแผงนั้น
        notes: ข้อความสรุปสั้น ๆ ใต้หัวข้อ
    """
    t0 = time.time()
    built, bad = [], []
    for i, p in enumerate(panels or [], 1):
        rid = p.get("result_id")
        got = _RESULTS.get(rid) if rid else None
        if got is None:
            bad.append("แผงที่ %d อ้าง result_id %r ซึ่งไม่มีอยู่" % (i, rid))
            continue
        built.append({"rows": got["rows"], "sql": got.get("sql", ""),
                      "warnings": got.get("warnings", []),
                      "chart_type": p.get("chart_type", "bar"),
                      "x": p.get("x"), "y": p.get("y"),
                      "title": p.get("title", "แผงที่ %d" % i)})

    if not built:
        out = {"ok": False, "code": "RESULT_NOT_FOUND",
               "error": "ไม่มีแผงไหนใช้ได้ " + " ".join(bad),
               "how_to_fix": "เรียก query_complaints ให้ครบทุกมุมมองก่อน "
                             "แล้วเอา result_id ที่ได้มาใส่ในแต่ละแผง",
               "available_result_ids": list(_RESULT_ORDER)}
    else:
        try:
            out = _dashboard(title, built, notes)
            if bad:
                out.setdefault("warnings", []).extend(bad)
        except EXPECTED as e:
            out = _recoverable(
                e, "x และ y ต้องเป็นชื่อคอลัมน์ที่มีอยู่จริงในผลลัพธ์ "
                   "และ chart_type ต้องเป็นหนึ่งใน bar, barh, line, pie",
                valid_chart_types=["bar", "barh", "line", "pie"])
        except Exception as e:
            _unexpected("generate_dashboard", e)
    _log_call("generate_dashboard",
              {"title": title, "panels": len(panels or [])}, out, t0)
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
        "RAG_MODE=%s" % os.environ.get("RAG_MODE", "off"),
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
    # พิมพ์โหมดไว้ใน log ตั้งแต่ตอนเริ่ม จะได้ไม่วัดผลสลับกันโดยไม่รู้ตัว
    print("[start] RAG_MODE=%s  RERANK_MODEL=%s" % (
        os.environ.get("RAG_MODE", "off"),
        os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")),
        file=sys.stderr, flush=True)
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(
            transport="streamable-http",
            host=os.environ.get("MCP_HOST", "0.0.0.0"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )
