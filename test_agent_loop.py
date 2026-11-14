"""
test_agent_loop.py - ทดสอบ agent loop โดยไม่ต้องมีคีย์ OpenRouter

ใช้โมเดลจำลองที่สั่งเรียก tool ตามสคริปต์ที่เขียนไว้
พิสูจน์ว่า
  1. agent เรียก tool ผ่าน MCP ได้จริง
  2. ผลลัพธ์ถูกส่งกลับเข้า messages ถูกต้อง
  3. context ที่ต้องใช้วัด faithfulness ถูกเก็บครบ

    python3 test_agent_loop.py
"""

import asyncio
import json
import types

import agent
from observability import tracer


def _msg(content=None, tool_calls=None):
    """สร้าง object ให้หน้าตาเหมือนที่ OpenAI SDK คืนมา"""
    m = types.SimpleNamespace(content=content, tool_calls=tool_calls)
    m.model_dump = lambda exclude_none=True: (
        {"role": "assistant", "content": content} if not tool_calls else
        {"role": "assistant", "tool_calls": [
            {"id": c.id, "type": "function",
             "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in tool_calls]})
    return m


def _call(cid, name, args):
    return types.SimpleNamespace(
        id=cid,
        function=types.SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeLLM:
    """เล่นตามบท 3 ตา: แปลงคำ -> สืบค้น -> ตอบ"""

    def __init__(self):
        self.step = 0
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kw):
        self.step += 1
        if self.step == 1:
            msg = _msg(tool_calls=[_call("c1", "resolve_filter_values",
                                         {"user_phrase": "AIS"})])
        elif self.step == 2:
            msg = _msg(tool_calls=[_call("c2", "query_complaints", {
                "group_by": ["year"],
                "filters": {"provider": ["บริษัท แอดวานซ์ ไวร์เลส เน็ทเวอร์ค จำกัด"]},
                "limit": 3})])
        else:
            msg = _msg(content="ตอบเรียบร้อย (คำตอบจำลอง)")
        return types.SimpleNamespace(
            choices=[types.SimpleNamespace(message=msg)],
            usage=types.SimpleNamespace(total_tokens=123))


async def main():
    question = "AIS ถูกร้องเรียนกี่เรื่องในแต่ละปี"

    async with agent.open_session() as session:
        system, oa_tools = await agent.build_context(session)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": question}]

        answer = await agent.run_turn(session, FakeLLM(), messages, oa_tools, question)

    print("\nคำตอบ:", answer)

    print("\nลำดับข้อความใน messages")
    for m in messages:
        role = m["role"] if isinstance(m, dict) else "?"
        if role == "system":
            print("  system  (system prompt ยาว %d ตัวอักษร)" % len(m["content"]))
        elif role == "assistant" and m.get("tool_calls"):
            print("  assistant  ขอเรียก:",
                  ", ".join(c["function"]["name"] for c in m["tool_calls"]))
        elif role == "tool":
            print("  tool    ผลลัพธ์ %d ตัวอักษร" % len(m["content"]))
        else:
            print("  %-9s %s" % (role, str(m.get("content"))[:60]))

    print("\ncontext ที่เก็บไว้ใช้วัด faithfulness: %d ชิ้น" % len(tracer.context))
    for c in tracer.context:
        body = json.loads(c["output"]) if c["output"].startswith("{") else {}
        if "sql_used" in body:
            print("  %-22s sql: %s" % (c["tool"], body["sql_used"][:80]))
            print("  %-22s rows: %s" % ("", json.dumps(body["rows"], ensure_ascii=False)[:90]))
            for w in body.get("warnings", []):
                print("  %-22s เตือน: %s" % ("", w[:70]))
        else:
            print("  %-22s confidence=%s %d ตัวเลือก"
                  % (c["tool"], body.get("confidence"), len(body.get("candidates", []))))


if __name__ == "__main__":
    asyncio.run(main())
