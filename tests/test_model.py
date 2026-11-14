"""
ทดสอบว่าโมเดลไหนเรียก tool เป็นจริง ก่อนจะเอาไปรัน eval 90 ครั้ง

ที่มาของปัญหา
    qwen-2.5-72b พิมพ์ !function_call:{...} ออกมาเป็นข้อความธรรมดา
    แทนที่จะเรียก tool ผ่าน API จริง ทำให้ agent ไม่เคยได้ข้อมูลจากฐานข้อมูล
    อาการแบบนี้ไม่เห็นจนกว่าจะรันจริง จึงควรยิงทดสอบตัวละครั้งก่อน

วิธีใช้
    python -m tests.test_model
    python -m tests.test_model google/gemini-2.5-flash openai/gpt-4.1-mini
"""

import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv()

# ตัวที่ใช้อยู่ใส่ไว้เป็นฐานเปรียบเทียบ
DEFAULT_MODELS = [
    "qwen/qwen-2.5-72b-instruct",
    "google/gemini-2.5-flash",
    "openai/gpt-4.1-mini",
    "anthropic/claude-3.5-haiku",
    "deepseek/deepseek-chat",
]

TOOLS = [{
    "type": "function",
    "function": {
        "name": "query_complaints",
        "description": "นับจำนวนเรื่องร้องเรียนจากฐานข้อมูล",
        "parameters": {
            "type": "object",
            "properties": {
                "metric": {"type": "string", "enum": ["count", "avg_days_to_close"]},
                "group_by": {"type": "array", "items": {"type": "string"}},
                "filters": {"type": "object"},
            },
            "required": ["metric"],
        },
    },
}, {
    "type": "function",
    "function": {
        "name": "resolve_filter_values",
        "description": "แปลงคำที่ผู้ใช้พิมพ์เป็นค่าจริงในฐานข้อมูล",
        "parameters": {
            "type": "object",
            "properties": {
                "user_phrase": {"type": "string"},
                "field": {"type": "string"},
            },
            "required": ["user_phrase"],
        },
    },
}]

QUESTION = "ปี 2564 จังหวัดไหนร้องเรียนมากที่สุด 5 อันดับ"
SYSTEM = ("ตอบเป็นภาษาไทย ห้ามเดาตัวเลขเอง ต้องเรียก tool เสมอ "
          "ฐานข้อมูลเก็บปีเป็น พ.ศ. คอลัมน์ที่ใช้ได้คือ year, province")


def check(client, model: str) -> dict:
    t0 = time.time()
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": QUESTION}],
            tools=TOOLS, tool_choice="auto")
    except Exception as e:
        return {"model": model, "ok": False, "why": "%s: %s" % (type(e).__name__, str(e)[:90])}

    ms = (time.time() - t0) * 1000
    if not getattr(resp, "choices", None):
        err = getattr(resp, "error", None)
        return {"model": model, "ok": False, "why": "ไม่มี choices กลับมา %s" % str(err)[:80]}

    msg = resp.choices[0].message
    calls = msg.tool_calls or []
    text = msg.content or ""

    if not calls:
        # อาการของ qwen คือพิมพ์คำสั่งเรียก tool ออกมาเป็นข้อความแทน
        fake = any(k in text for k in ("function_call", "!function", '"name":'))
        return {"model": model, "ok": False, "ms": ms,
                "why": "พิมพ์ออกมาเป็นข้อความ ไม่ได้เรียก tool จริง" if fake
                       else "ไม่เรียก tool เลย ตอบเป็นข้อความ",
                "text": text[:100]}

    c = calls[0]
    name = c.function.name
    try:
        args = json.loads(c.function.arguments)
        bad_json = None
    except Exception as e:
        args, bad_json = {}, str(e)[:60]

    return {"model": model, "ok": bad_json is None, "ms": ms, "tool": name,
            "args": args, "why": bad_json or "", "n_calls": len(calls)}


def main():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        print("ไม่พบ OPENROUTER_API_KEY ใน .env")
        return
    from openai import OpenAI

    client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
    models = sys.argv[1:] or DEFAULT_MODELS

    print("คำถามทดสอบ: %s\n" % QUESTION)
    for m in models:
        r = check(client, m)
        mark = "ผ่าน " if r["ok"] else "ไม่ผ่าน"
        print("%-7s %-34s %s" % (mark, m, ("%.0f ms" % r["ms"]) if "ms" in r else ""))
        if r.get("tool"):
            print("        เรียก %s  args=%s" % (
                r["tool"], json.dumps(r.get("args", {}), ensure_ascii=False)[:120]))
        if r.get("why"):
            print("        %s" % r["why"])
        if r.get("text"):
            print("        ตอบมาว่า: %s" % r["text"].replace("\n", " "))
        print()


if __name__ == "__main__":
    main()
