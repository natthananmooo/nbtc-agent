"""
ตรวจ contract ของ tool โดยยิงค่าผิดผ่าน MCP จริง

คำถามที่ต้องการคำตอบ
    เมื่อ LLM ส่งค่าผิดชนิดหรือผิดช่วงเข้ามา ใครเป็นคนปฏิเสธ
      ก  ชั้น protocol ปฏิเสธก่อนเข้า tool  -> LLM ได้ error ดิบ แก้ต่อไม่ได้
      ข  โค้ดของเราปฏิเสธเอง                -> LLM ได้ code กับ how_to_fix แก้ต่อได้
    แบบ ข คือสิ่งที่ต้องการ ถ้าเจอแบบ ก ต้องย้ายการตรวจเข้ามาในฟังก์ชัน

วิธีรัน ต้องให้ MCP Server ขึ้นอยู่ก่อน
    docker compose up -d mcp
    python -m tests.test_contract
"""

import asyncio
import json

import agent

CASES = [
    ("query_complaints", {"metric": "count", "limit": "100"},
     "limit เป็นสตริงแทนตัวเลข"),
    ("query_complaints", {"metric": "count", "limit": 0},
     "limit นอกช่วง"),
    ("query_complaints", {"metric": "count", "limit": 999999},
     "limit ใหญ่เกิน"),
    ("query_complaints", {"metric": "count", "group_by": "province"},
     "group_by เป็นสตริงแทนลิสต์"),
    ("query_complaints", {"metric": "ไม่มี metric นี้"},
     "metric ที่ไม่มีอยู่"),
    ("query_complaints", {"metric": "count", "group_by": ["ไม่มีคอลัมน์"]},
     "คอลัมน์ที่ไม่มีอยู่"),
    ("query_complaints", {"metric": "count", "order_by": "count desc"},
     "order_by ที่เคยทำให้พลาดจริง"),
    ("analyze_results", {"result_id": "ไม่มีรหัสนี้"},
     "result_id ที่ไม่มีอยู่"),
    ("explore_relations", {"value": "ไม่มีค่านี้จริง"},
     "ค่าที่ไม่มีในกราฟ"),
    ("resolve_filter_values", {"user_phrase": "AIS", "field": "ไม่มีคอลัมน์"},
     "field ที่ไม่มีอยู่"),
]


def verdict(payload) -> str:
    """บอกว่าใครเป็นคนปฏิเสธ และ LLM เอาไปแก้ต่อได้ไหม"""
    if payload is None:
        return "ชั้น protocol ปฏิเสธ  LLM แก้ต่อไม่ได้"
    if not isinstance(payload, dict):
        return "คืนค่าที่ไม่ใช่ object  ตรวจไม่ได้"
    if payload.get("ok") is False:
        code = payload.get("code") or "ไม่มี code"
        fix = "มี how_to_fix" if payload.get("how_to_fix") else "ไม่มี how_to_fix"
        return "โค้ดเราปฏิเสธ  code=%s  %s" % (code, fix)
    return "ผ่านเข้าไปได้ ไม่ถูกปฏิเสธเลย"


async def main():
    async with agent.open_session() as session:
        print("%-22s %-32s %s" % ("tool", "กรณีทดสอบ", "ผลลัพธ์"))
        print("-" * 100)
        for tool, args, label in CASES:
            try:
                res = await session.call_tool(tool, args)
            except Exception as e:
                print("%-22s %-32s ชั้น protocol โยน %s: %s"
                      % (tool, label, type(e).__name__, str(e)[:40]))
                continue

            payload = None
            if getattr(res, "content", None):
                raw = getattr(res.content[0], "text", "")
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = raw
            flag = "  [is_error]" if getattr(res, "is_error", False) else ""
            print("%-22s %-32s %s%s" % (tool, label, verdict(payload), flag))


if __name__ == "__main__":
    asyncio.run(main())
