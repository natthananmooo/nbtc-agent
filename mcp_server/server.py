"""
mcp_server/server.py - ห่อ Application Tools ทั้ง 4 ตัวให้เป็น MCP Server

ตัวนี้คือส่วน Server ของ MCP ที่แท้จริง
มันรันแยก process จาก agent และประกาศ tool ของตัวเองผ่านโปรโตคอล
ไม่ใช่การเรียกฟังก์ชัน Python ภายในโปรเซสเดียวกัน

ตรรกะทั้งหมดอยู่ใน tools/ ไฟล์นี้แค่ประกาศให้โลกภายนอกรู้จัก
ถ้าวันหนึ่งเลิกใช้ MCP ก็ import tools/ ตรง ๆ ได้โดยไม่ต้องแก้อะไร
"""
# ส่วนที่ 1: นำเข้าไลบรารี
# - MCPServer: เปลี่ยนฟังก์ชัน Python เป็น MCP Tool และเปิดเซิร์ฟเวอร์ให้
# - tools/: ตรรกะจริงทั้งหมด ไฟล์นี้แค่ห่อให้เรียกผ่านโปรโตคอลได้
from __future__ import annotations  # เปิดให้เขียน type hint แบบใหม่ เช่น str | None

import json  # แปลง dict เป็นข้อความตอนพิมพ์ log
import os  # อ่าน environment variable เช่น MCP_LOG และ RAG_MODE
import sys  # ใช้ sys.stderr เป็นปลายทางของ log
import time  # จับเวลาที่แต่ละ tool ใช้
import traceback  # พิมพ์ stack trace เมื่อเจอบั๊กของเราเอง

from mcp.server.mcpserver import MCPServer  # ตัวสร้าง MCP Server ของ SDK รุ่น 2.x

from tools.analyze import analyze_results as _analyze  # คำนวณสถิติจากแถวที่ดึงมาแล้ว
from tools.chart import render_chart as _chart  # วาดกราฟหนึ่งรูป
from tools.dashboard import generate_dashboard as _dashboard  # รวมหลายกราฟเป็นหน้าเดียว
from tools.graph import explore_relations as _relations  # เดินกราฟความสัมพันธ์
from tools.query import query_complaints as _query  # ประกอบ SQL แล้วรันแบบอ่านอย่างเดียว
from tools.resolve import resolve_filter_values as _resolve  # แปลคำผู้ใช้เป็นค่าจริง
from tools.resolve import values_for_prompt as _values  # รายการค่าทุกคอลัมน์ สำหรับ resource
from tools import db as _db  # whitelist คอลัมน์ และการต่อฐานข้อมูลแบบอ่านอย่างเดียว

# --------------------------------------------------------------- ข้อผิดพลาด
# MCP ซ่อนรายละเอียด exception ไม่ให้ไปถึง client โมเดลจึงเห็นแค่
# "Error executing tool ..." ซึ่งแก้ตัวเองไม่ได้
# จึงดักเองแล้วคืนเป็นผลลัพธ์ธรรมดาที่บอกว่าผิดตรงไหนและต้องทำอย่างไร
# โมเดลอ่านแล้วเรียกใหม่ให้ถูกได้ในรอบถัดไป


# ------------------------------------------------------------------- log
# ส่วนที่ 2: การบันทึก log
# พิมพ์ว่าใครเรียก tool อะไร ด้วยพารามิเตอร์ไหน และได้อะไรกลับ
# เพื่อให้ docker compose logs -f mcp อ่านแล้วเห็นการทำงานจริง
LOG = os.environ.get("MCP_LOG", "1") != "0"  # ปิด log ได้ด้วย MCP_LOG=0


# หน้าที่: ย่อค่าใด ๆ ให้เป็นข้อความสั้นพอใส่ log หนึ่งบรรทัด
# ใช้ ensure_ascii=False เพื่อให้ภาษาไทยอ่านออกใน log ไม่ใช่ ก
# รับ: ค่าใดก็ได้ และความยาวสูงสุด · คืน: ข้อความที่ถูกตัดแล้ว
def _brief(v, limit=90) -> str:
    t = json.dumps(v, ensure_ascii=False, default=str)  # default=str กันค่าที่แปลงเป็น JSON ไม่ได้
    return t if len(t) <= limit else t[:limit] + "…"  # ยาวเกินก็ตัดแล้วเติมจุดไข่ปลา


# หน้าที่: พิมพ์บรรทัด log หนึ่งชุดต่อการเรียก tool หนึ่งครั้ง
# เลือกแสดงรายละเอียดตามชนิดของผลลัพธ์ เพราะแต่ละ tool คืนคีย์ไม่เหมือนกัน
# รับ: ชื่อ tool, พารามิเตอร์, ผลลัพธ์, เวลาที่เริ่ม · คืน: ไม่คืนอะไร พิมพ์ลง stderr
def _log_call(name: str, args: dict, result, started: float):
    if not LOG:  # ปิด log ไว้ก็ออกเลย ไม่ต้องเสียเวลาประกอบข้อความ
        return
    ms = (time.time() - started) * 1000  # เวลาที่ใช้ หน่วยมิลลิวินาที
    shown = {k: v for k, v in args.items() if v not in (None, [], {})}  # ตัดพารามิเตอร์ที่ไม่ได้ส่งมาออก
    line = "[tool] %-22s %s" % (name, _brief(shown, 120))  # บรรทัดหลัก ชื่อ tool กับพารามิเตอร์

    if isinstance(result, dict):  # ผลลัพธ์ปกติเป็น dict เสมอ แต่กันไว้เผื่อไม่ใช่
        if result.get("ok") is False:  # ผิดพลาดแบบแก้ต่อได้ แสดงแค่ข้อความ error
            line += "\n        ผิดพลาด  %s" % result.get("error", "")[:110]
        else:
            if "sql_used" in result:  # มาจาก query_complaints
                line += "\n        SQL      %s" % result["sql_used"][:110]
                line += "\n        ได้      %d แถว" % result.get("row_count", 0)
            if "confidence" in result:  # มาจาก resolve_filter_values
                line += "\n        ได้      confidence=%s  %d ตัวเลือก" % (
                    result["confidence"], len(result.get("candidates", [])))
            if "related" in result:  # มาจาก explore_relations โหมด related
                line += "\n        ได้      ความสัมพันธ์ %s" % ", ".join(
                    "%s %d ค่า" % (k, len(v)) for k, v in result["related"].items())
            if "similar" in result:  # มาจาก explore_relations โหมด similar
                line += "\n        ได้      ค่าใกล้เคียง %d ตัว เทียบบน %s" % (
                    len(result["similar"]), result.get("compared_on"))
            if "findings" in result:  # มาจาก analyze_results
                line += "\n        ได้      %d ข้อสรุป" % len(result["findings"])
            if "image_path" in result:  # มาจาก render_chart
                line += "\n        ได้      %s" % result.get("image_path")
            if "html_path" in result:  # มาจาก generate_dashboard
                line += "\n        ได้      %s (%d แผง)" % (
                    result.get("html_path"), result.get("panels_drawn", 0))
            for w in (result.get("warnings") or [])[:3]:  # แสดงคำเตือนไม่เกิน 3 ข้อ
                line += "\n        เตือน    %s" % w[:110]

    print("%s  %.0f ms" % (line, ms), file=sys.stderr, flush=True)  # stderr เพื่อไม่ปนกับคำตอบของ MCP


# ------------------------------------------------------- ผลลัพธ์ที่อ้างถึงได้
# ส่วนที่ 3: คลังผลลัพธ์ชั่วคราว
# เดิม analyze_results กับ render_chart ต้องให้โมเดลก๊อป rows ทั้งก้อนส่งกลับมา
# ซึ่งพลาดง่ายมาก ของจริงพบว่าโมเดลส่งสตริง "{%call_result%...}" มาแทนข้อมูล
# จึงเก็บผลไว้ฝั่ง server แล้วให้อ้างด้วย result_id สั้น ๆ แทน
_RESULTS: dict[str, dict] = {}  # เก็บผลลัพธ์ตาม result_id
_RESULT_ORDER: list[str] = []  # ลำดับที่เก็บ ใช้หาว่าอันไหนเก่าสุดเวลาต้องทิ้ง
_KEEP = 20  # เก็บผลลัพธ์ล่าสุดไว้กี่ชุด เกินนี้ทิ้งอันเก่าสุด กันหน่วยความจำบวม


# หน้าที่: เก็บผลลัพธ์หนึ่งชุด แล้วคืนรหัสสั้น ๆ ให้โมเดลอ้างถึงทีหลัง
# รับ: dict ที่มี rows, warnings, sql · คืน: รหัสแบบ r1 r2 r3
def _remember(payload: dict) -> str:
    rid = "r%d" % (len(_RESULT_ORDER) + 1)  # นับต่อไปเรื่อย ๆ ไม่ใช้เลขซ้ำแม้จะทิ้งของเก่าไปแล้ว
    _RESULTS[rid] = payload
    _RESULT_ORDER.append(rid)
    while len(_RESULT_ORDER) > _KEEP:  # เกินเพดานก็ทิ้งอันเก่าสุดออกทีละตัว
        _RESULTS.pop(_RESULT_ORDER.pop(0), None)
    return rid


# หน้าที่: หาแถวข้อมูลให้ tool ที่ทำงานต่อจากผลลัพธ์เดิม
# รับได้สองทาง คือ result_id ที่เก็บไว้ฝั่ง server หรือ rows ที่โมเดลส่งมาเอง
# ทางแรกปลอดภัยกว่ามาก ทางที่สองเก็บไว้เผื่อกรณีที่โมเดลมีข้อมูลอยู่แล้วจริง ๆ
# รับ: result_id และ rows · คืน: (rows, warnings, ข้อความผิดพลาด)
def _rows_from(result_id, rows):
    """คืน (rows, warnings, ข้อความผิดพลาด)"""
    if result_id:  # ทางที่ถูกต้อง อ้างด้วยรหัส
        got = _RESULTS.get(result_id)
        if got is None:  # รหัสไม่มีอยู่ อาจถูกทิ้งไปแล้วหรือโมเดลเดาเอง
            return None, None, ("ไม่พบ result_id %r ที่มีอยู่คือ %s"
                                % (result_id, ", ".join(_RESULT_ORDER) or "ยังไม่มี"))
        return got["rows"], got.get("warnings", []), None
    if isinstance(rows, list) and all(isinstance(r, dict) for r in rows):  # ทางสำรอง ต้องเป็นลิสต์ของ dict จริง
        return rows, None, None
    # ข้อความนี้ยาวเพราะต้องสอนโมเดลว่าพลาดตรงไหนและทางที่ง่ายกว่าคืออะไร
    return None, None, ("rows ต้องเป็นลิสต์ของ object ที่ได้จาก query_complaints "
                        "ห้ามส่งข้อความอ้างอิงอย่าง {%call_result%...} "
                        "วิธีที่ง่ายกว่าคือใส่ result_id ที่ query_complaints คืนมา")


# ส่วนที่ 4: การจัดการข้อผิดพลาดสองระดับ
# LVL 1 แก้ต่อได้: คืน dict ที่มี ok, code, error, how_to_fix ให้ LLM ลองใหม่เอง
# LVL 2 เป็นบั๊กของเรา: พิมพ์ traceback แล้ว raise ต่อ เพราะ LLM แก้ให้ไม่ได้
# ความผิดพลาดที่คาดไว้แล้วและ LLM แก้ต่อเองได้ เช่นส่งชื่อคอลัมน์ผิด
# ต้องแยกจากบั๊กของเราเอง ไม่งั้น KeyError ที่เกิดจากโค้ดเราเขียนผิด
# จะถูกห่อเป็นคำแนะนำส่งให้ LLM แล้ว LLM ก็ลองใหม่วนไป โดยไม่มีใครเห็นว่ามีบั๊ก
EXPECTED = (_db.SchemaError, ValueError)  # ประกาศครั้งเดียว ใช้ร่วมกันทั้ง 6 tool


# หน้าที่: สร้าง dict รูปแบบ error ที่ตกลงกันไว้ ให้ทุก tool คืนหน้าตาเหมือนกัน
# hints เป็นคีย์เวิร์ดอาร์กิวเมนต์ เพื่อให้แต่ละ tool แนบค่าที่ใช้ได้ของตัวเองไปด้วย
# รับ: exception, คำแนะนำ, รหัส, ค่าที่ใช้ได้ · คืน: {ok, code, error, how_to_fix, ...}
def _recoverable(err: Exception, how_to_fix: str, code: str = "INVALID_ARG",
                 **hints) -> dict:
    """ระดับที่หนึ่ง ผิดแล้วแก้ต่อได้ คืนเป็นผลลัพธ์ปกติให้ LLM อ่านแล้วลองใหม่"""
    return {
        "ok": False,  # บอกชัดว่าไม่สำเร็จ โมเดลเช็คคีย์นี้ก่อนใช้ผล
        "code": code,  # รหัสสำหรับโปรแกรม เช่น INVALID_ARG
        "error": str(err) or type(err).__name__,  # ข้อความอธิบาย ถ้าว่างก็ใช้ชื่อคลาสแทน
        "how_to_fix": how_to_fix,  # คำแนะนำให้ LLM แก้แล้วเรียกใหม่
        **hints,  # ค่าที่ใช้ได้จริง เช่น valid_fields หรือ valid_modes
    }


# หน้าที่: จัดการข้อผิดพลาดที่ไม่คาดคิด ซึ่งแปลว่าโค้ดเราเขียนผิด
# ต้อง raise ต่อ ห้ามกลืน ไม่งั้นบั๊กจะถูกห่อเป็นคำแนะนำแล้วไม่มีใครเห็น
# รับ: ชื่อ tool และ exception · คืน: ไม่คืน เพราะ raise ออกไปเสมอ
def _unexpected(name: str, err: Exception):
    """ระดับที่สอง ไม่คาดคิด แปลว่าเป็นบั๊กของเรา ต้องบันทึกแล้วโยนต่อให้เห็น"""
    print("[bug] %s ล้มด้วย %s: %s" % (name, type(err).__name__, err),
          file=sys.stderr, flush=True)  # ขึ้นต้นด้วย [bug] เพื่อให้ค้นใน log เจอง่าย
    traceback.print_exc()  # พิมพ์ stack trace เต็มเพื่อให้รู้ว่าพังบรรทัดไหน
    raise err  # โยนต่อ ให้เห็นว่าระบบมีปัญหาจริง


# ส่วนที่ 5: สร้าง MCP Server
# instructions คือคำอธิบายของ server ที่ client อ่านได้ตอน initialize
mcp = MCPServer(
    "nbtc-complaints",  # ชื่อ server ที่ประกาศให้ client รู้จัก
    instructions="สถิติเรื่องร้องเรียนในกิจการโทรคมนาคม จาก กสทช. "
                 "42,806 เรื่อง ปี 2552 ถึง 2569 ฐานข้อมูลเปิดแบบอ่านอย่างเดียว",
)


# ส่วนที่ 6: Tool ทั้ง 6 ตัว
# decorator @mcp.tool() ลงทะเบียนฟังก์ชันเข้า server และสร้าง JSON Schema
# จากชื่อฟังก์ชัน docstring และ type hint ให้เอง
# docstring คือสิ่งที่ LLM อ่านเพื่อตัดสินใจว่าจะเรียก tool ไหน จึงเขียนยาวโดยตั้งใจ
# tool name: resolve_filter_values
# tool argument: user_phrase (บังคับ), field (ไม่บังคับ)
# หน้าที่: แปลงคำที่ผู้ใช้พิมพ์เป็นค่าที่มีอยู่จริงในฐานข้อมูล พร้อมระดับความมั่นใจ
# รับ: คำผู้ใช้ และชื่อคอลัมน์ถ้ารู้ · คืน: dict ที่มี confidence กับ use_in_filters
@mcp.tool()
def resolve_filter_values(
    user_phrase: str,  # tool argument · คำที่ผู้ใช้พิมพ์ เช่น AIS มือถือ ภาคเหนือ
    field: str | None = None,  # tool argument · ชื่อคอลัมน์ถ้ารู้แน่ เว้นว่างจะค้นทุกคอลัมน์
) -> dict:
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
    t0 = time.time()  # จับเวลาไว้ตั้งแต่ต้น เพื่อรายงานใน log
    try:
        out = _resolve(user_phrase, field)  # ตรรกะจริงอยู่ใน tools/resolve.py
    except EXPECTED as e:  # LVL 1 เช่นส่งชื่อคอลัมน์ที่ไม่มีอยู่
        out = _recoverable(
            e, "ตรวจชื่อ field ให้ตรงกับรายการที่ให้ไว้ หรือเว้นว่างเพื่อค้นทุกคอลัมน์",
            valid_fields=list(_db.DIMENSIONS))  # แนบรายชื่อคอลัมน์ที่ใช้ได้ไปด้วย
    except Exception as e:  # LVL 2 บั๊กของเรา
        _unexpected("resolve_filter_values", e)
    _log_call("resolve_filter_values",
              {"user_phrase": user_phrase, "field": field}, out, t0)
    return out


# tool name: query_complaints
# tool argument: metric, group_by, filters, date_from, date_to, order_by, limit (ไม่บังคับทั้งหมด)
# หน้าที่: ประกอบ SQL จากพารามิเตอร์แล้วรันบนฐานข้อมูลที่เปิดแบบอ่านอย่างเดียว
# LLM ไม่เคยเขียน SQL เอง ส่งมาแค่พารามิเตอร์ ซึ่งเป็นชั้นแรกของกลไกอ่านอย่างเดียว
# รับ: metric, group_by, filters, ช่วงวันที่, order_by, limit · คืน: dict ที่มี rows, sql_used, warnings
@mcp.tool()
def query_complaints(
    metric: str = "count",  # tool argument · count, avg_days_to_close หรือ share_pct
    group_by: list[str] | str | None = None,  # tool argument · รับทั้งลิสต์และสตริง เพราะโมเดลส่งมาทั้งสองแบบ
    filters: dict | str | None = None,  # tool argument · รับทั้ง dict และสตริง JSON ด้วยเหตุผลเดียวกัน
    date_from: str | None = None,  # tool argument · กรองจากวันรับเรื่อง
    date_to: str | None = None,  # tool argument · กรองถึงวันรับเรื่อง
    order_by: str | None = None,  # tool argument · เว้นว่างจะเรียงตามคอลัมน์ที่จัดกลุ่ม
    limit: int = 50000,  # tool argument · ตั้งเท่าเพดาน เพื่อไม่ให้ผลถูกตัดโดยผู้ใช้ไม่ได้สั่ง
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
        limit: จำนวนกลุ่มสูงสุดที่คืนกลับ ค่าเริ่มต้นคือเพดาน 50000 จึงไม่ถูกตัด
    """
    t0 = time.time()
    try:
        out = _query(metric, group_by, filters, date_from, date_to, order_by, limit)
    except _db.SchemaError as e:  # ชื่อคอลัมน์หรือ metric ไม่อยู่ใน whitelist
        out = _recoverable(
            e, "เลือกชื่อคอลัมน์หรือ metric จากรายการข้างล่าง แล้วเรียกใหม่",
            valid_columns=list(_db.DIMENSIONS), valid_metrics=list(_db.MEASURES))
    except EXPECTED as e:  # รูปแบบพารามิเตอร์ผิด เช่น filters เป็นข้อความธรรมดา
        out = _recoverable(
            e, "ตรวจรูปแบบพารามิเตอร์ เช่น group_by ต้องเป็นลิสต์ของชื่อคอลัมน์ "
               "filters ต้องเป็น dict ชื่อคอลัมน์ไปหาลิสต์ของค่า "
               "date_from และ date_to ต้องเป็น YYYY-MM-DD โดยปีเป็น พ.ศ.",
            valid_columns=list(_db.DIMENSIONS), valid_metrics=list(_db.MEASURES))
    except Exception as e:  # LVL 2 บั๊กของเรา
        _unexpected("query_complaints", e)
    if isinstance(out, dict) and out.get("ok") is not False:  # สำเร็จเท่านั้นจึงเก็บไว้ให้อ้างต่อ
        out["result_id"] = _remember({"rows": out.get("rows", []),
                                      "warnings": out.get("warnings", []),
                                      "sql": out.get("sql_used", "")})
    _log_call("query_complaints",
              {"metric": metric, "group_by": group_by, "filters": filters,
               "date_from": date_from, "date_to": date_to,
               "order_by": order_by, "limit": limit}, out, t0)
    return out


# tool name: explore_relations
# tool argument: value (บังคับ), field, to_field, mode, top_k (ไม่บังคับ)
# หน้าที่: เดินกราฟความสัมพันธ์ที่สร้างจากฐานข้อมูล ตอบว่าค่าหนึ่งพบร่วมกับอะไรบ่อย
# ใช้ตอนยังไม่รู้ว่าควรกรองด้วยอะไร ไม่ใช่ตัวที่เอาไปตอบตัวเลขให้ผู้ใช้
# รับ: ค่า, คอลัมน์ต้นทาง, คอลัมน์ปลายทาง, โหมด, จำนวนสูงสุด · คืน: dict ที่มี lift และ share_pct
@mcp.tool()
def explore_relations(
    value: str,  # tool argument · ค่าจริงในฐานข้อมูล ต้องผ่าน resolve_filter_values มาก่อน
    field: str | None = None,  # tool argument · คอลัมน์ของค่านั้น เว้นว่างได้ ระบบจะหาให้
    to_field: str | None = None,  # tool argument · อยากดูไปยังคอลัมน์ไหน เว้นว่างจะคืนทุกคอลัมน์
    mode: str = "related",  # tool argument · related คือพบร่วมกันบ่อย similar คือรูปแบบคล้ายกัน
    top_k: int = 8,  # tool argument · จำนวนรายการสูงสุดต่อคอลัมน์
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
        out = _relations(value, field, to_field, mode, top_k)  # ตรรกะจริงอยู่ใน tools/graph.py
    except EXPECTED as e:  # ค่าไม่อยู่ในกราฟ หรือ mode ผิด
        out = _recoverable(
            e, "ตรวจว่า value เป็นค่าจริงที่ได้จาก resolve_filter_values "
               "และ mode เป็น related หรือ similar เท่านั้น",
            valid_modes=["related", "similar"],
            valid_fields=list(_db.DIMENSIONS))
    except Exception as e:  # LVL 2 บั๊กของเรา
        _unexpected("explore_relations", e)
    _log_call("explore_relations",
              {"value": value, "field": field, "to_field": to_field, "mode": mode}, out, t0)
    return out


# tool name: analyze_results
# tool argument: result_id, rows, question, mode (ไม่บังคับทั้งหมด แต่ต้องมี result_id หรือ rows อย่างใดอย่างหนึ่ง)
# หน้าที่: คำนวณสถิติจากแถวที่ query_complaints ดึงมาแล้ว ไม่ไปหาข้อมูลใหม่
# แยกออกมาเป็น tool ต่างหาก เพื่อไม่ให้โมเดลคำนวณเองแล้วได้เลขที่สืบที่มาไม่ได้
# รับ: result_id หรือ rows, คำถามเดิม, โหมด · คืน: dict ที่มี findings และ caveats
@mcp.tool()
def analyze_results(
    result_id: str | None = None,  # tool argument · ทางที่ถูกต้อง อ้างผลลัพธ์ด้วยรหัส
    rows: list[dict] | None = None,  # tool argument · ทางสำรอง ส่งแถวมาเอง
    question: str = "",  # tool argument · คำถามเดิมของผู้ใช้ ใช้เลือกมุมที่จะสรุป
    mode: str = "summary",  # tool argument · summary, trend, compare หรือ share
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
    data, warns, err = _rows_from(result_id, rows)  # หาแถวข้อมูลก่อน ยังไม่คำนวณอะไร
    if err:  # หาไม่เจอ คืน error ทันทีโดยไม่ต้องเข้า try
        out = {"ok": False, "code": "RESULT_NOT_FOUND", "error": err,
               "how_to_fix": "เรียก query_complaints ก่อน แล้วส่ง result_id ที่ได้มาที่นี่",
               "available_result_ids": list(_RESULT_ORDER)}  # บอกไปเลยว่ามีรหัสอะไรให้ใช้บ้าง
    else:
        try:
            out = _analyze(data, question, mode, warns)  # ตรรกะจริงอยู่ใน tools/analyze.py
        except EXPECTED as e:  # mode ไม่อยู่ในรายการ
            out = _recoverable(
                e, "mode ต้องเป็นหนึ่งใน summary, trend, compare, share",
                valid_modes=["summary", "trend", "compare", "share"])
        except Exception as e:  # LVL 2 บั๊กของเรา
            _unexpected("analyze_results", e)
    _log_call("analyze_results",
              {"result_id": result_id, "mode": mode, "rows": len(data or [])}, out, t0)
    return out


# tool name: render_chart
# tool argument: chart_type, x, y, title (บังคับ), result_id, rows, filename (ไม่บังคับ)
# หน้าที่: วาดกราฟหนึ่งรูปจากผลลัพธ์ที่ดึงมาแล้ว คืน path ของไฟล์ภาพ
# รับ: ชนิดกราฟ, แกน x, แกน y, หัวกราฟ, result_id หรือ rows, ชื่อไฟล์ · คืน: dict ที่มี image_path
@mcp.tool()
def render_chart(
    chart_type: str,  # tool argument · bar, barh, line หรือ pie
    x: str,  # tool argument · ชื่อคอลัมน์หมวดหมู่
    y: str,  # tool argument · ชื่อคอลัมน์ตัวเลข
    title: str,  # tool argument · หัวกราฟ
    result_id: str | None = None,  # tool argument · อ้างผลลัพธ์ด้วยรหัส
    rows: list[dict] | None = None,  # tool argument · ทางสำรอง ส่งแถวมาเอง
    filename: str | None = None,  # tool argument · เว้นว่างระบบตั้งชื่อให้เอง
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
    data, _, err = _rows_from(result_id, rows)  # ตัวนี้ไม่ใช้ warnings จึงทิ้งด้วย _
    if err:
        out = {"ok": False, "code": "RESULT_NOT_FOUND", "error": err,
               "how_to_fix": "เรียก query_complaints ก่อน แล้วส่ง result_id ที่ได้มาที่นี่",
               "available_result_ids": list(_RESULT_ORDER)}
    else:
        try:
            out = _chart(data, chart_type, x, y, title, filename)  # ตรรกะจริงอยู่ใน tools/chart.py
        except EXPECTED as e:  # ชื่อคอลัมน์ไม่มีใน rows หรือชนิดกราฟผิด
            out = _recoverable(
                e, "x และ y ต้องเป็นชื่อคอลัมน์ที่มีอยู่จริงใน rows "
                   "และ chart_type ต้องเป็นหนึ่งใน bar, barh, line, pie",
                columns_in_rows=list(data[0]) if data else [],  # บอกไปเลยว่าใน rows มีคอลัมน์อะไร
                valid_chart_types=["bar", "barh", "line", "pie"])
        except Exception as e:  # LVL 2 บั๊กของเรา
            _unexpected("render_chart", e)
    _log_call("render_chart",
              {"result_id": result_id, "chart_type": chart_type, "x": x, "y": y}, out, t0)
    return out


# tool name: generate_dashboard
# tool argument: title, panels (บังคับ), notes (ไม่บังคับ)
# หน้าที่: รวมหลายกราฟเป็นหน้า HTML เดียว พร้อมแสดง SQL ใต้แต่ละแผง
# แผงที่อ้าง result_id ผิดจะถูกข้ามไปแต่ยังสร้างหน้าให้ ถ้าเหลืออย่างน้อยหนึ่งแผงที่ใช้ได้
# รับ: หัวข้อ, รายการแผง, ข้อความสรุป · คืน: dict ที่มี html_path และ panels_drawn
@mcp.tool()
def generate_dashboard(
    title: str,  # tool argument · หัวข้อหน้าสรุป
    panels: list[dict],  # tool argument · แต่ละแผงมี result_id, chart_type, x, y, title
    notes: list[str] | None = None,  # tool argument · ข้อความสรุปสั้น ๆ ใต้หัวข้อ
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
    built, bad = [], []  # built คือแผงที่ใช้ได้ bad คือข้อความบอกว่าแผงไหนพัง
    for i, p in enumerate(panels or [], 1):  # เริ่มนับที่ 1 เพื่อให้ข้อความอ่านเป็นภาษาคน
        rid = p.get("result_id")
        got = _RESULTS.get(rid) if rid else None
        if got is None:  # แผงนี้อ้างรหัสที่ไม่มีอยู่ ข้ามไปแต่จำไว้บอกทีหลัง
            bad.append("แผงที่ %d อ้าง result_id %r ซึ่งไม่มีอยู่" % (i, rid))
            continue
        built.append({"rows": got["rows"], "sql": got.get("sql", ""),
                      "warnings": got.get("warnings", []),
                      "chart_type": p.get("chart_type", "bar"),  # ไม่ระบุก็ใช้ bar
                      "x": p.get("x"), "y": p.get("y"),
                      "title": p.get("title", "แผงที่ %d" % i)})  # ไม่ตั้งชื่อก็ใช้เลขแผง

    if not built:  # ไม่เหลือแผงที่ใช้ได้เลย จึงไม่สร้างหน้าว่าง ๆ ให้
        out = {"ok": False, "code": "RESULT_NOT_FOUND",
               "error": "ไม่มีแผงไหนใช้ได้ " + " ".join(bad),
               "how_to_fix": "เรียก query_complaints ให้ครบทุกมุมมองก่อน "
                             "แล้วเอา result_id ที่ได้มาใส่ในแต่ละแผง",
               "available_result_ids": list(_RESULT_ORDER)}
    else:
        try:
            out = _dashboard(title, built, notes)  # ตรรกะจริงอยู่ใน tools/dashboard.py
            if bad:  # สร้างสำเร็จแต่มีบางแผงหาย ต้องบอกผู้ใช้ด้วย
                out.setdefault("warnings", []).extend(bad)
        except EXPECTED as e:  # ชื่อคอลัมน์หรือชนิดกราฟผิด
            out = _recoverable(
                e, "x และ y ต้องเป็นชื่อคอลัมน์ที่มีอยู่จริงในผลลัพธ์ "
                   "และ chart_type ต้องเป็นหนึ่งใน bar, barh, line, pie",
                valid_chart_types=["bar", "barh", "line", "pie"])
        except Exception as e:  # LVL 2 บั๊กของเรา
            _unexpected("generate_dashboard", e)
    _log_call("generate_dashboard",
              {"title": title, "panels": len(panels or [])}, out, t0)
    return out


# ---------------------------------------------------------------- resource
# ส่วนที่ 7: resource
# รายการค่าที่มีจริงในฐานข้อมูล ส่งให้ agent เอาไปใส่ system prompt
# เป็น resource ไม่ใช่ tool เพราะเป็นข้อมูลบริบท ไม่ใช่การกระทำ
# agent จึงไม่ต้องเข้าถึงไฟล์ฐานข้อมูลเองเลย ซึ่งเป็นจุดสำคัญด้านความปลอดภัย
# resource name: schema://values  (เป็น resource ไม่ใช่ tool จึงไม่มี tool argument)
# หน้าที่: ประกอบข้อความอธิบายโครงสร้างตารางและค่าที่มีอยู่จริงทุกคอลัมน์
# รับ: ไม่รับพารามิเตอร์ · คืน: ข้อความหลายบรรทัด ที่ agent เอาไปต่อใน system prompt
@mcp.resource("schema://values", mime_type="text/plain")
def schema_values() -> str:
    """รายการค่าไม่ซ้ำของแต่ละคอลัมน์ และคำอธิบายโครงสร้างตาราง"""
    # ถามช่วงปีจากข้อมูลจริง ไม่ฝังเลขปีไว้ในโค้ด ข้อมูลอัปเดตแล้วข้อความนี้ตามเอง
    years = _db.fetch("SELECT MIN(year) a, MAX(year) b FROM complaints WHERE year IS NOT NULL")[0]
    per_year = _db.fetch(
        "SELECT year, COUNT(*) n FROM complaints WHERE year IS NOT NULL GROUP BY year ORDER BY year")

    lines = [
        "ตาราง complaints มี %d แถว" % _db.total_rows(),
        "",
        # บอกให้ชัดว่าเก็บเป็น พ.ศ. เพราะเป็นจุดที่โมเดลมักเข้าใจผิดแล้วลบ 543 เอง
        "ปีที่มีข้อมูล เก็บเป็น พ.ศ. ตามที่ กสทช. เผยแพร่ ตั้งแต่ %d ถึง %d"
        % (years["a"], years["b"]),
        "  " + "  ".join("%d:%d" % (r["year"], r["n"]) for r in per_year),  # ปี:จำนวนเรื่อง
        "ผู้ใช้พิมพ์ พ.ศ. มาได้เลย ตรงกับที่เก็บ ถ้าพิมพ์ ค.ศ. มา query_complaints แปลงขึ้นให้",
        # ประกาศโหมดไว้ในนี้ด้วย เพื่อให้ run_eval ตรวจได้ว่ารันถูกคอนฟิกหรือเปล่า
        "RAG_MODE=%s" % os.environ.get("RAG_MODE", "off"),
        "วันที่เก็บรูปแบบ ปปปป-ดด-วว โดยปีเป็น พ.ศ. เช่น 2552-01-05",
        "คอลัมน์ received_date_raw และ closed_date_raw เก็บสตริงต้นฉบับไว้ครบ",
        "เดือนเก็บเป็นตัวเลข 1 ถึง 12",
        "",
        "คอลัมน์ที่กรองและจัดกลุ่มได้",
    ]
    for col, desc in _db.DIMENSIONS.items():  # ดึงจาก whitelist ตัวเดียวกับที่ใช้ตรวจ SQL
        lines.append("  %-14s %s" % (col, desc))
    lines += ["", "ค่าที่วัดได้ (metric)"]
    for m, desc in _db.MEASURES.items():
        lines.append("  %-18s %s" % (m, desc))
    lines += ["", "ค่าที่มีอยู่จริงในแต่ละคอลัมน์", _values()]  # ส่วนที่ยาวที่สุด ค่าจริงทุกคอลัมน์
    return "\n".join(lines)


# ส่วนที่ 8: จุดเริ่มทำงาน
# รันเป็นสองโหมดได้ stdio สำหรับต่อตรงกับ client ในเครื่อง
# และ streamable-http สำหรับให้ agent ต่อผ่านเครือข่าย ซึ่งเป็นแบบที่ใช้จริง
if __name__ == "__main__":
    # พิมพ์โหมดไว้ใน log ตั้งแต่ตอนเริ่ม จะได้ไม่วัดผลสลับกันโดยไม่รู้ตัว
    print("[start] RAG_MODE=%s  RERANK_MODEL=%s" % (
        os.environ.get("RAG_MODE", "off"),
        os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")),
        file=sys.stderr, flush=True)
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")  # ค่าเริ่มต้นคือแบบที่ใช้จริง
    if transport == "stdio":
        mcp.run(transport="stdio")  # โหมดนี้ไม่เปิดพอร์ต คุยผ่าน stdin stdout
    else:
        mcp.run(
            transport="streamable-http",
            host=os.environ.get("MCP_HOST", "0.0.0.0"),  # 0.0.0.0 ให้เข้าถึงจากนอก container ได้
            port=int(os.environ.get("MCP_PORT", "8000")),
        )
