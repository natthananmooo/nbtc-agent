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
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agent
from observability import tracer

app = FastAPI(title="NBTC Complaint Insight Agent")
PAGE = (Path(__file__).resolve().parent / "index.html").read_text(encoding="utf-8")

# กราฟที่ mcp วาดไว้ถูก mount มาที่โฟลเดอร์เดียวกัน เสิร์ฟให้หน้าเว็บดูได้
CHART_DIR = Path(os.environ.get("CHART_DIR",
                                Path(__file__).resolve().parent.parent / "charts"))
CHART_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/charts", StaticFiles(directory=str(CHART_DIR)), name="charts")


class Ask(BaseModel):
    question: str
    # ไอดีบทสนทนา หน้าเว็บส่งกลับมาเพื่อให้ agent จำคำถามก่อนหน้าได้
    # ถ้าไม่ส่งมา ถือว่าเริ่มบทสนทนาใหม่
    chat_id: str | None = None


# เก็บประวัติไว้ฝั่ง server เพราะกลไกถามกลับจะไร้ประโยชน์ถ้าผู้ใช้ตอบกลับไม่ได้
# agent ถามว่าหมายถึงบริษัทไหน แล้วผู้ใช้พิมพ์ "ข้อ 3" ต้องมีบริบทเดิมจึงจะเข้าใจ
_CHATS: dict[str, list[dict]] = {}
_CHAT_ORDER: list[str] = []
_KEEP_CHATS = 50        # จำนวนบทสนทนาที่เก็บพร้อมกัน เกินนี้ทิ้งอันเก่าสุด
_KEEP_TURNS = 24        # จำนวนข้อความย้อนหลัง ไม่รวม system กันไม่ให้ prompt ยาวเกิน


def _history(chat_id: str | None, system: str) -> tuple[str, list[dict]]:
    """คืนไอดีบทสนทนากับรายการข้อความ สร้างใหม่ถ้ายังไม่มี"""
    if chat_id and chat_id in _CHATS:
        msgs = _CHATS[chat_id]
        msgs[0] = {"role": "system", "content": system}
        return chat_id, msgs

    cid = uuid.uuid4().hex[:12]
    _CHATS[cid] = [{"role": "system", "content": system}]
    _CHAT_ORDER.append(cid)
    while len(_CHAT_ORDER) > _KEEP_CHATS:
        _CHATS.pop(_CHAT_ORDER.pop(0), None)
    return cid, _CHATS[cid]


def _trim(msgs: list[dict]) -> None:
    """ตัดข้อความเก่าทิ้ง แต่ห้ามตัดจนข้อความแรกเป็น tool ที่ไม่มี tool_calls นำหน้า
    เพราะ OpenAI API จะปฏิเสธทั้งคำขอ"""
    body = msgs[1:]
    if len(body) <= _KEEP_TURNS:
        return
    body = body[-_KEEP_TURNS:]
    while body and body[0].get("role") in ("tool", "assistant"):
        body.pop(0)
    msgs[1:] = body


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
        method = body.get("method")
        # บอกให้ชัดว่าหาค่าเจอด้วยวิธีไหน จะได้เห็นว่าชั้นเวกเตอร์ทำงานตอนไหน
        out["how"] = ("ไฟล์ alias" if body.get("alias_group")
                      else "เป็นตัวเลข ไม่ต้องแปลง" if conf == "not_needed"
                      else "ไม่พบค่านี้ในฐานข้อมูล" if conf == "none"
                      else "เทียบความหมายด้วยเวกเตอร์" if method == "vector"
                      else "ตัดคำไทยแล้วนับคำซ้ำ BM25" if method == "bm25"
                      else "เทียบข้อความ")
        out["method"] = method
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

    elif step["tool"] == "explore_relations":
        out["how"] = "เดินกราฟความสัมพันธ์ที่สร้างจากฐานข้อมูล"
        out["mode"] = body.get("mode")
        out["related"] = body.get("related")
        out["similar"] = body.get("similar")

    elif step["tool"] == "analyze_results":
        out["how"] = "คำนวณจากแถวที่ดึงมา"
        out["findings"] = body.get("findings", [])
        out["caveats"] = body.get("caveats", [])

    elif step["tool"] == "render_chart":
        out["how"] = "วาดกราฟ"
        path = body.get("image_path")
        out["image_path"] = path
        # path ที่ได้เป็นพาธภายใน container แปลงเป็น URL ที่เบราว์เซอร์เปิดได้
        out["image_url"] = ("/charts/" + Path(path).name) if path else None
        out["warnings"] = body.get("warnings", [])

    elif step["tool"] == "generate_dashboard":
        out["how"] = "รวมหลายกราฟเป็นหน้าสรุป"
        hp = body.get("html_path")
        out["html_url"] = ("/charts/" + Path(hp).name) if hp else None
        out["panels_drawn"] = body.get("panels_drawn", 0)
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
            chat_id, messages = _history(req.chat_id, system)
            _trim(messages)
            messages.append({"role": "user", "content": req.question})
            answer = await agent.run_turn(session, client, messages, oa_tools, req.question)
            steps = [_label(s) for s in tracer.context]

    except Exception as e:
        tb = traceback.format_exc()
        print(tb, flush=True)          # ให้โผล่ใน docker compose logs web ด้วย
        return {"error": "%s: %s" % (type(e).__name__, e),
                "detail": tb[-1500:],
                "steps": [_label(s) for s in tracer.context],
                "mcp_url": agent.MCP_URL, "model": agent.MODEL}

    return {"question": req.question, "answer": answer, "chat_id": chat_id,
            "turns": len([m for m in messages if m.get("role") == "user"]),
            "steps": steps, "model": agent.MODEL, "logged": tracer.enabled}
