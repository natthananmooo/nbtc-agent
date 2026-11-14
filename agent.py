"""
agent.py - Agent Core

ส่วนที่ตัดสินใจ อยู่ที่นี่ที่เดียว
  - จะเรียก Tool ตัวไหน เมื่อไร และกี่ครั้ง
  - เมื่อค่ากำกวม จะถามกลับแทนการเดา

สองการเชื่อมต่อที่ไม่เกี่ยวกัน
  agent -> OpenRouter  ผ่าน HTTPS แบบ OpenAI function calling
  agent -> MCP Server  ผ่านโปรโตคอล MCP
OpenRouter ไม่รู้จัก MCP หน้าที่ของ MCP Client คือแปลง schema ให้ OpenRouter เข้าใจ

วิธีรัน
    docker compose up -d --build        เปิด MCP Server ก่อน
    python agent.py                     โหมดถาม-ตอบ
    python agent.py "ปี 2564 จังหวัดไหนร้องเรียนมากสุด 5 อันดับ"
    python agent.py --dry               ดู system prompt กับ tool โดยไม่เรียก LLM
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from observability import tracer

load_dotenv()

MCP_URL = os.environ.get("MCP_URL", "http://127.0.0.1:8000/mcp")
MODEL = os.environ.get("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
MAX_STEPS = int(os.environ.get("AGENT_MAX_STEPS", "8"))

SYSTEM_PROMPT = """คุณเป็นผู้ช่วยวิเคราะห์สถิติเรื่องร้องเรียนในกิจการโทรคมนาคมของ กสทช.
ตอบเป็นภาษาไทย กระชับ และอ้างอิงตัวเลขจากฐานข้อมูลเท่านั้น

กฎการตัดสินใจ ทำตามลำดับนี้เสมอ

0. เรื่องปี ห้ามลืม
   ฐานข้อมูลเก็บปีเป็น พ.ศ. ตามที่ กสทช. เผยแพร่ คือ 2552 ถึง 2569
   ผู้ใช้พิมพ์ พ.ศ. มาก็ส่งเข้า filters ได้เลย ไม่ต้องแปลง
   ถ้าผู้ใช้พิมพ์ ค.ศ. มา ส่งเข้าไปได้เหมือนกัน เครื่องมือแปลงขึ้นเป็น พ.ศ. ให้เอง
   ตอบกลับผู้ใช้เป็น พ.ศ. เพราะเป็นหน่วยเดียวกับที่ถามและกับที่ กสทช. เผยแพร่
   ห้ามเรียก resolve_filter_values กับ year หรือ month เพราะเป็นตัวเลข ไม่ใช่ข้อความ
   ห้ามบอกว่าไม่มีข้อมูลปีนั้นก่อนที่จะลองค้นจริง รายการปีที่มีอยู่ครบอยู่ท้าย prompt นี้แล้ว

1. ถ้าคำถามอ้างถึงผู้ให้บริการ ประเภทบริการ กลุ่มปัญหา หรือพื้นที่
   ให้เรียก resolve_filter_values ก่อน อย่าเดาค่าเอง
   คำที่คนพูดมักไม่ตรงกับค่าที่เก็บจริง เช่น ผู้ใช้พิมพ์ AIS
   แต่ฐานข้อมูลเก็บว่า บริษัท แอดวานซ์ ไวร์เลส เน็ทเวอร์ค จำกัด

2. ดูค่า confidence ที่ได้
   high      ใช้ค่านั้น และบอกผู้ใช้ด้วยว่าใช้ค่าอะไร
             ถ้ามี alias_group ต้องบอกว่ารวมบริษัทใดเข้าด้วยกันบ้าง
   ambiguous หยุดทันที ห้ามเรียก query_complaints
             ให้ถามกลับว่าผู้ใช้หมายถึงตัวเลือกไหน พร้อมบอกจำนวนเรื่องของแต่ละตัวเลือก
   none      ห้ามเดา ให้ถามผู้ใช้ใหม่ หรือบอกว่าไม่มีข้อมูลนี้ในฐานข้อมูล

3. ถ้าคำถามกำกวมในเรื่องอื่น เช่น ไม่ระบุช่วงเวลา หรือไม่ระบุว่าจะดูรายปีหรือรายเดือน
   ให้ถามกลับก่อน อย่าเลือกให้เอง

4. เมื่อค่าชัดแล้ว เรียก query_complaints
   ห้ามส่ง SQL เข้าไป ส่งเฉพาะพารามิเตอร์
   ใส่ filters จากฟิลด์ use_in_filters ที่ resolve_filter_values คืนมา ทั้งก้อน
   ห้ามหยิบไปแค่ค่าเดียวแล้วอ้างว่ารวมค่าอื่นด้วย

   ห้ามเพิ่มตัวกรองที่ผู้ใช้ไม่ได้ขอ โดยเฉพาะปี
   ถ้าผู้ใช้ไม่ได้ระบุปี แปลว่าต้องการทุกปี อย่าใส่ year ลงไปเอง
   ถ้าคิดว่าควรดูเฉพาะปีล่าสุด ให้ถามผู้ใช้ก่อน

   query_complaints จะคืน result_id มาด้วย เก็บไว้ใช้ขั้นถัดไป

5. ถ้าต้องสรุปแนวโน้มหรือเปรียบเทียบ ให้เรียก analyze_results
   ส่ง result_id ที่ได้จาก query_complaints ไม่ต้องก๊อป rows กลับมา
   ห้ามส่งข้อความอ้างอิงอย่าง {%call_result%...} เพราะไม่มีการแทนค่าให้
   อย่าคิดเลขเอง ให้ใช้ตัวเลขที่ tool คำนวณมา

6. เรียก render_chart เฉพาะเมื่อผู้ใช้ขอกราฟ ส่ง result_id เช่นกัน

กฎการตอบ

- ห้ามตอบตัวเลขใด ๆ ถ้ายังไม่ได้เรียก query_complaints ในรอบนี้
  ตัวเลข rows ที่ resolve_filter_values คืนมาเป็นยอดรวมทุกปีไว้ให้ดูประกอบเท่านั้น
  ห้ามเอามาบวกกันเองแล้วตอบ เพราะถ้าคำถามมีเงื่อนไขอื่น เช่นระบุปี จะผิดทันที
- ทุกตัวเลขในคำตอบต้องมาจากผลลัพธ์ของ tool ห้ามประมาณหรือเติมเอง
- ห้ามคำนวณเอง ไม่ว่าจะบวก ลบ หาผลรวม หรือหาร้อยละ ให้ tool คำนวณให้
- ห้ามบอกว่า "ข้อมูลมีแค่ปีนั้นปีนี้" จากการเดา ให้ดูรายการปีที่ให้ไว้ท้าย prompt
- ต้องแจ้ง warnings ทุกข้อที่ tool คืนมา โดยเฉพาะเรื่องข้อมูลที่ถูกตัดออก
- ปิดท้ายคำตอบด้วยบรรทัด "ที่มา: " แล้วตามด้วย sql_used ที่ใช้จริง
- ถ้าข้อมูลไม่พอจะตอบ ให้บอกตรง ๆ ว่าตอบไม่ได้ ดีกว่าเดา
- ห้ามอ้างว่าผลลัพธ์รวมค่าใดไว้ ถ้าค่านั้นไม่ได้อยู่ใน filters ของ SQL ที่รันจริง
- ถ้า tool คืน ok เป็น false ให้อ่าน how_to_fix แล้วเรียกใหม่ให้ถูก ห้ามเดาคำตอบแทน

โครงสร้างข้อมูลและค่าที่มีอยู่จริง
{schema}
"""


def mcp_tools_to_openai(tools) -> list[dict]:
    """แปลง schema ที่ MCP Server ประกาศ ให้อยู่ในรูปที่ OpenRouter เข้าใจ"""
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": (t.description or "").strip(),
            "parameters": t.input_schema,
        },
    } for t in tools]


def _root_cause(e: BaseException) -> str:
    """ดึงสาเหตุจริงออกจาก ExceptionGroup ที่ anyio ห่อไว้หลายชั้น"""
    while True:
        subs = getattr(e, "exceptions", None)
        if not subs:
            break
        e = subs[0]
    return "%s: %s" % (type(e).__name__, e) if str(e) else type(e).__name__


async def _http_available() -> tuple[bool, str]:
    """ลองต่อ MCP Server ผ่าน HTTP หนึ่งครั้ง เพื่อตัดสินว่าจะใช้ทางไหน"""
    try:
        async with streamable_http_client(MCP_URL) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
        return True, ""
    except BaseException as e:
        return False, _root_cause(e)


@asynccontextmanager
async def open_session():
    """
    ต่อ MCP Server ผ่าน HTTP ถ้าไม่ได้ ค่อยเปิดเป็น subprocess แบบ stdio

    ตรวจการเชื่อมต่อให้จบก่อน แล้วค่อยเปิดของจริง
    ไม่ครอบ yield ด้วย try เพราะถ้าครอบ ข้อผิดพลาดของผู้เรียกจะถูกกลืน
    แล้วเด้งไปเปิด server ซ้ำโดยไม่จำเป็น
    """
    ok, why = await _http_available()

    if ok:
        print("[ต่อ MCP Server ผ่าน HTTP ที่ %s]" % MCP_URL, file=sys.stderr)
        async with streamable_http_client(MCP_URL) as (read, write):
            async with ClientSession(read, write) as s:
                await s.initialize()
                yield s
        return

    print("[ต่อ HTTP ที่ %s ไม่ได้ -> %s]" % (MCP_URL, why), file=sys.stderr)
    print("[เปิด MCP Server แบบ stdio แทน ใช้งานได้ปกติ แต่ไม่มี read-only ชั้นที่ 4]",
          file=sys.stderr)

    env = dict(os.environ, MCP_TRANSPORT="stdio", PYTHONPATH=os.getcwd())
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "mcp_server.server"], env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as s:
            await s.initialize()
            yield s


async def build_context(session) -> tuple[str, list[dict]]:
    """ดึง tool กับ resource จาก MCP Server มาประกอบเป็นบริบทของ agent"""
    tools = (await session.list_tools()).tools
    res = await session.read_resource("schema://values")
    schema = res.contents[0].text
    # ใช้ replace ไม่ใช้ format
    # เพราะทั้ง prompt และข้อมูล schema มีวงเล็บปีกกาปนอยู่ได้
    # เช่น ตัวอย่าง filters={"year": [2564]} หรือคำเตือนเรื่อง placeholder
    # ซึ่ง str.format จะเข้าใจผิดว่าเป็นตัวแปรแล้วโยน KeyError
    return SYSTEM_PROMPT.replace("{schema}", schema), mcp_tools_to_openai(tools)


async def run_turn(session, client, messages: list[dict], oa_tools: list[dict],
                   question: str) -> str:
    """วนรอบจนกว่าโมเดลจะตอบเป็นข้อความ แทนที่จะขอเรียก tool เพิ่ม"""
    with tracer.question(question):
        answer = "หยุดเพราะเรียก tool เกิน %d รอบแล้วยังไม่ได้คำตอบ" % MAX_STEPS

        for _ in range(MAX_STEPS):
            visible = [m for m in messages if m.get("role") != "system"][-8:]
            with tracer.generation(MODEL, visible) as gen:
                resp = client.chat.completions.create(
                    model=MODEL, messages=messages, tools=oa_tools, tool_choice="auto")
                msg = resp.choices[0].message
                usage = getattr(resp, "usage", None)
                gen.done(
                    msg.content or [c.function.name for c in (msg.tool_calls or [])],
                    tokens=getattr(usage, "total_tokens", None),
                )

            messages.append(msg.model_dump(exclude_none=True))

            if not msg.tool_calls:
                answer = msg.content or "(โมเดลไม่ได้ตอบอะไร)"
                break

            for call in msg.tool_calls:
                name = call.function.name
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                print("  [เรียก %s %s]"
                      % (name, json.dumps(args, ensure_ascii=False)[:160]), file=sys.stderr)

                with tracer.tool_call(name, args) as span:
                    result = await session.call_tool(name, args)
                    text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
                    if result.is_error:
                        text = "tool ทำงานไม่สำเร็จ: " + text
                    span.done(text, is_error=result.is_error)

                messages.append({"role": "tool", "tool_call_id": call.id, "content": text})

        tracer.finish(question, answer)
        return answer


async def main():
    args = [a for a in sys.argv[1:]]
    dry = "--dry" in args
    args = [a for a in args if a != "--dry"]

    async with open_session() as session:
        system, oa_tools = await build_context(session)

        if dry:
            print(system)
            print("\n=== tool ที่ MCP Server ประกาศ ===")
            for t in oa_tools:
                print(" -", t["function"]["name"])
            return

        print("[Langfuse: %s]" % ("เปิดใช้งาน" if tracer.enabled
              else "ยังไม่ได้ใส่คีย์ จะไม่บันทึก"), file=sys.stderr)

        key = os.environ.get("OPENROUTER_API_KEY")
        if not key:
            print("ยังไม่ได้ตั้ง OPENROUTER_API_KEY ใน .env\n"
                  "คัดลอก .env.example เป็น .env แล้วใส่คีย์ก่อน\n"
                  "หรือรัน python agent.py --dry เพื่อดูบริบทโดยไม่เรียกโมเดล")
            return

        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")

        messages: list[dict] = [{"role": "system", "content": system}]
        questions = [" ".join(args)] if args else None

        while True:
            if questions:
                q = questions.pop(0)
                print("\nคำถาม:", q)
            else:
                try:
                    q = input("\nคำถาม (เว้นว่างเพื่อออก): ").strip()
                except (EOFError, KeyboardInterrupt):
                    break
                if not q:
                    break
            messages.append({"role": "user", "content": q})
            print(await run_turn(session, client, messages, oa_tools, q))
            if args and not questions:
                break


if __name__ == "__main__":
    asyncio.run(main())
