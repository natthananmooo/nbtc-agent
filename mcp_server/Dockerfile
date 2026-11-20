FROM python:3.11-slim

# fonts-thai-tlwg ทำให้ matplotlib วาดตัวอักษรไทยได้ ไม่เป็นสี่เหลี่ยม
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-thai-tlwg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY mcp_server/requirements.txt ./requirements.txt

# ลง torch รุ่น CPU จาก index ของ pytorch เอง
# ถ้าปล่อยให้ลงจาก pypi จะได้รุ่น CUDA ที่ใหญ่กว่าสิบเท่า ทั้งที่ไม่มีการ์ดจอให้ใช้
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# ดาวน์โหลดโมเดลจัดอันดับฝังไว้ใน image ตั้งแต่ตอน build
# ตอนรันจึงไม่ต้องต่ออินเทอร์เน็ต และได้โมเดลเวอร์ชันเดิมทุกครั้งที่รัน
ARG RERANK_MODEL=BAAI/bge-reranker-v2-m3
ENV RERANK_MODEL=${RERANK_MODEL}
RUN python -c "from sentence_transformers import CrossEncoder; CrossEncoder('${RERANK_MODEL}', max_length=256)"

# โมเดลฝังข้อความของชั้นค้นกฎหมาย ต้องเป็นตัวเดียวกับที่ใช้ตอนทำดัชนีเท่านั้น
# ถ้าใช้คนละตัว เวกเตอร์ของคำถามกับเวกเตอร์ในฐานข้อมูลจะอยู่คนละปริภูมิ
# ผลค้นจะออกมามั่วโดยไม่มี error ให้เห็น ซึ่งเป็นบั๊กที่หาสาเหตุยากที่สุดแบบหนึ่ง
ARG EMBED_MODEL=BAAI/bge-m3
ENV EMBED_MODEL=${EMBED_MODEL}
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}')"

# เรียกตัวตัดคำไทยหนึ่งครั้งตอน build ให้มันเตรียมพจนานุกรมไว้
# ตอนรันปิดเน็ตแล้ว ถ้าเพิ่งมาเตรียมตอนนั้นอาจพลาดได้
RUN python -c "from pythainlp.tokenize import word_tokenize; word_tokenize('ทดสอบระบบ', engine='newmm')"

ENV HF_HUB_OFFLINE=1

COPY tools/ ./tools/
COPY mcp_server/ ./mcp_server/
# ชั้นค้นกฎหมาย คนละโมดูลกับ tools/ เพราะใช้ฐานข้อมูลคนละตัวและโหลดเมื่อถูกเรียกเท่านั้น
COPY lawrag/ ./lawrag/
COPY tests/ ./tests/
COPY aliases.yaml ./aliases.yaml

# ฐานข้อมูลไม่ได้ copy เข้า image แต่ mount เข้ามาแบบอ่านอย่างเดียวตอนรัน
ENV COMPLAINTS_DB=/data/complaints.db
ENV CHART_DIR=/charts
# โหมดการค้นค่า off / bm25 / vector ใช้วัดผลเทียบกันสามขา
ENV RAG_MODE=off
# ชั้นค้นกฎหมาย อ่านเวกเตอร์จาก Postgres และอ่านดัชนี BM25 กับกราฟจากโฟลเดอร์ที่ mount เข้ามา
ENV PGDSN=postgresql://law:law@pgvec:5432/law
ENV LAW_DIR=/data/law
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000
CMD ["python", "-m", "mcp_server.server"]
