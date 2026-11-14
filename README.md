# NBTC Complaint Insight Agent

ระบบผู้ช่วยวิเคราะห์เรื่องร้องเรียนกิจการโทรคมนาคมด้วย Agentic AI
รับคำถามภาษาไทย แปลงเป็นการสืบค้นฐานข้อมูลแบบอ่านอย่างเดียว แล้วสรุปพร้อมแหล่งที่มาของตัวเลข

ข้อมูลจาก [ชุดข้อมูลสถิติเรื่องร้องเรียนในกิจการโทรคมนาคม กสทช.](https://datacatalog.nbtc.go.th/dataset/dataset_12_05)
42,806 เรื่อง ปี 2552 ถึง 2569

## สถาปัตยกรรม

```
ผู้ใช้ (ภาษาไทย)
      |
Agent Core  (agent.py)                 --- HTTPS ---> OpenRouter
  ตัดสินใจว่าจะเรียก tool ไหน                          (function calling)
  มี MCP Client แปลง schema
      |
      | MCP protocol
      v
MCP Server  (Docker container)
  resolve_filter_values
  query_complaints
  analyze_results
  render_chart
      |
      v
complaints.db   mount แบบ :ro
```

จุดสำคัญคือ **การตัดสินใจอยู่ที่ Agent Core ที่เดียว** ส่วน tool ทำงานที่กำหนดไว้เท่านั้น
และ process ของ agent ไม่เห็นไฟล์ฐานข้อมูลเลย

## กลไกอ่านอย่างเดียว 4 ชั้น

| ชั้น | กลไก | ใครบังคับ |
| --- | --- | --- |
| 1 | LLM ไม่ได้เขียน SQL ส่งแค่พารามิเตอร์ | โครงสร้างโค้ด |
| 2 | ชื่อคอลัมน์ผ่าน whitelist ค่าผ่าน parameterized query | `tools/db.py` |
| 3 | `sqlite3.connect("file:...?mode=ro")` | SQLite driver |
| 4 | `volumes: ./data/complaints.db:/data/complaints.db:ro` | kernel ของ Docker |

ทดสอบชั้น 1 ถึง 3 ได้ด้วย `python3 -m tools.db`

## วิธีรัน

### 1. เตรียมฐานข้อมูล (ทำครั้งเดียว)

```bash
python3 01_fetch_explore.py     # ดึงข้อมูลจาก CKAN API ของ กสทช.
python3 02_build_db.py          # สร้าง data/complaints.db
```

### 2. เปิด MCP Server

```bash
docker compose up -d --build
docker compose logs -f mcp      # ดูว่าขึ้นแล้ว
```

### 3. ตั้งค่าและรัน agent

```bash
cp .env.example .env            # แล้วใส่ OPENROUTER_API_KEY
pip install -r requirements.txt

python3 agent.py                            # โหมดถาม-ตอบ
python3 agent.py "ปี 2564 จังหวัดไหนร้องเรียนมากสุด 5 อันดับ"
python3 agent.py --dry                      # ดู system prompt โดยไม่เรียก LLM
```

## ตัวอย่างคำถาม

| คำถาม | สิ่งที่ระบบทำ |
| --- | --- |
| ปี 2564 จังหวัดไหนร้องเรียนมากสุด 5 อันดับ | สืบค้นตรง ๆ แล้วแจ้งว่าข้อมูลจังหวัดขาดหาย 51.3% |
| AIS ถูกร้องเรียนกี่เรื่อง | แปลง AIS เป็นชื่อนิติบุคคล 4 บริษัท แล้วบอกว่ารวมอะไรบ้าง |
| เน็ตช้ามีคนร้องเรียนเยอะไหม | กำกวม ระบบถามกลับก่อน ไม่เดา |

## ทดสอบ

```bash
python3 -m tools.db            # กลไกอ่านอย่างเดียว
python3 -m tools.resolve       # การแปลงคำผู้ใช้
python3 -m tools.query         # การสืบค้นและ warnings
python3 -m tools.analyze       # การสรุปตัวเลข
python3 test_mcp_server.py     # คุยกับ MCP Server ผ่านโปรโตคอลจริง
python3 test_agent_loop.py     # agent loop ทั้งวง โดยไม่ต้องมีคีย์ OpenRouter
```

## โครงสร้างโฟลเดอร์

```
tools/            ตรรกะทั้งหมด เป็นฟังก์ชัน Python ล้วน ไม่ผูกกับ MCP หรือ OpenRouter
mcp_server/       ชั้นประกาศ tool ผ่านโปรโตคอล MCP + Dockerfile
agent.py          Agent Core ตัดสินใจ และเป็น MCP Client
aliases.yaml      แมปคำผู้ใช้เป็นค่าในฐานข้อมูล ไฟล์นี้คนเขียน ไม่ใช่ AI เดา
data/             ฐานข้อมูลและไฟล์ดิบ ไม่ขึ้น git สร้างใหม่ได้จากสคริปต์
```

## การประเมินผลด้วย Langfuse

ใส่คีย์ใน `.env` แล้วระบบจะบันทึกเองทุกคำถาม ถ้าไม่ใส่ก็รันได้ปกติ แค่ไม่บันทึก

```
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

สิ่งที่บันทึกต่อหนึ่งคำถาม

| ชนิด | ชื่อ | เก็บอะไร |
| --- | --- | --- |
| agent | ถาม-ตอบหนึ่งรอบ | คำถาม คำตอบ และ context ทั้งหมดที่ดึงมา |
| retriever | `resolve_filter_values` | คำที่พิมพ์ ค่าที่ได้ และ confidence |
| retriever | `query_complaints` | `sql_used` พารามิเตอร์ แถวที่ได้ และ warnings |
| tool | `analyze_results`, `render_chart` | อินพุตและเอาต์พุต |
| generation | `llm` | ข้อความที่ส่งเข้าโมเดล คำตอบ และจำนวน token |

สองตัวแรกบันทึกเป็น `retriever` เพราะเป็นขั้นตอนดึงข้อมูล
ทำให้เครื่องมือประเมินรู้ว่าอะไรคือ context ที่คำตอบต้องอ้างอิงได้

| ค่าที่วัด | วิธีวัด |
| --- | --- |
| correctness | เทียบคำตอบกับเฉลยที่คำนวณจาก SQL โดยตรง |
| faithfulness | เทียบทุกตัวเลขในคำตอบกับแถวที่ `query_complaints` คืนมา |

## สิ่งที่ยังไม่ทำ

- หน้าเว็บแสดงคำตอบพร้อมแถบบอกว่าไปค้นที่ไหน (จะรันใน Docker)
- ชุดคำถามทดสอบ 30 ข้อ พร้อมเฉลย สำหรับคิดคะแนน
- value resolution ด้วย vector เพื่อเทียบว่า correctness ดีขึ้นกี่เปอร์เซ็นต์
- dashboard รวมหลายกราฟ
