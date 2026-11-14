"""
web/app.py - หน้าเว็บถาม-ตอบ

แสดงคำตอบพร้อมแถบบอกว่าไปค้นอะไรมา
  1. แปลงคำผู้ใช้เป็นค่าในฐานข้อมูลได้อย่างไร
  2. SQL ที่รันจริง
  3. ข้อจำกัดของผลลัพธ์
  4. คำตอบ

    uvicorn web.app:app --reload --port 8080
"""

from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import agent
from observability import tracer

app = FastAPI(title="NBTC Complaint Insight Agent")
PAGE = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")


class Ask(BaseModel):
    question: str


def _label(step: dict) -> dict:
    """แปลงผลของ tool ให้เป็นข้อมูลที่หน้าเว็บแสดงได้"""
    try:
        body = json.loads(step["output"])
    except (json.JSONDecodeError, TypeError):
        return {"tool": step["tool"], "kind": step.get("kind"), "raw": str(step["output"])[:400]}

    out = {"tool": step["tool"], "kind": step.get("kind")}

    if isinstance(body, dict) and body.get("ok") is False:
        out["how"] = "เครื่องมือแจ้งว่าพารามิเตอร์ไม่ถูก"
        out["error"] = body.get("error")
        out["how_to_fix"] = body.get("how_to_fix")
        return out

    if step["tool"] == "resolve_filter_values":
        conf = body.get("confidence")
        out["how"] = ("ไฟล์ alias" if body.get("alias_group")
                      else "เป็นตัวเลข ไม่ต้องแปลง" if conf == "not_needed"
                      else "เทียบข้อความ" if conf in ("high", "ambiguous")
                      else "ไม่พบค่านี้ในฐานข้อมูล")
        out["note"] = body.get("note")
        out["confidence"] = conf
        out["field"] = body.get("field")
        out["query"] = body.get("query")
        out["candidates"] = body.get("candidates", [])[:6]
        out["field_values"] = body.get("field_values", [])[:8]

    elif step["tool"] == "query_complaints":
        out["how"] = "SQL บนฐานข้อมูลอ่านอย่างเดียว"
        out["sql"] = body.get("sql_used")
        out["params"] = body.get("params", [])
        out["rows"] = body.get("rows", [])[:12]
        out["row_count"] = body.get("row_count", 0)
        out["warnings"] = body.get("warnings", [])
        if out["row_count"] == 0:
            out["how"] = "ค้นแล้วไม่มีข้อมูลตรงเงื่อนไข"

    elif step["tool"] == "analyze_results":
        out["how"] = "คำนวณจากแถวที่ดึงมา"
        out["findings"] = body.get("findings", [])
        out["caveats"] = body.get("caveats", [])

    elif step["tool"] == "render_chart":
        out["how"] = "วาดกราฟ"
        out["image_path"] = body.get("image_path")
        out["warnings"] = body.get("warnings", [])

    return out


@app.get("/", response_class=HTMLResponse)
def index():
    return PAGE


@app.get("/api/health")
async def health():
    async with agent.open_session() as s:
        tools = (await s.list_tools()).tools
    return {"ok": True, "model": agent.MODEL,
            "langfuse": tracer.enabled, "tools": [t.name for t in tools]}


@app.post("/api/ask")
async def ask(req: Ask):
    """ไม่ปล่อยให้ error หลุดเป็น HTTP 500 เพราะหน้าเว็บจะได้ HTML แทน JSON
    แล้วขึ้นข้อความกำกวมว่า Unexpected token ซึ่งหาสาเหตุไม่ได้"""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return {"error": "ยังไม่ได้ตั้ง OPENROUTER_API_KEY",
                "detail": "service web อ่านคีย์จากไฟล์ .env ผ่าน env_file "
                          "ตรวจว่าไฟล์มีอยู่และมีบรรทัด OPENROUTER_API_KEY=sk-or-v1-..."}

    steps: list[dict] = []
    try:
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")

        async with agent.open_session() as session:
            system, oa_tools = await agent.build_context(session)
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": req.question}]
            answer = await agent.run_turn(session, client, messages, oa_tools, req.question)
            steps = [_label(s) for s in tracer.context]

    except Exception as e:
        tb = traceback.format_exc()
        print(tb, flush=True)          # ให้โผล่ใน docker compose logs web ด้วย
        return {"error": "%s: %s" % (type(e).__name__, e),
                "detail": tb[-1500:],
                "steps": [_label(s) for s in tracer.context],
                "mcp_url": agent.MCP_URL, "model": agent.MODEL}

    return {"question": req.question, "answer": answer,
            "steps": steps, "model": agent.MODEL, "logged": tracer.enabled}
