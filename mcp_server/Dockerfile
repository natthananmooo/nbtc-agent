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
ENV HF_HUB_OFFLINE=1

COPY tools/ ./tools/
COPY mcp_server/ ./mcp_server/
COPY tests/ ./tests/
COPY aliases.yaml ./aliases.yaml

# ฐานข้อมูลไม่ได้ copy เข้า image แต่ mount เข้ามาแบบอ่านอย่างเดียวตอนรัน
ENV COMPLAINTS_DB=/data/complaints.db
ENV CHART_DIR=/charts
# โหมดการค้นค่า off / bm25 / vector ใช้วัดผลเทียบกันสามขา
ENV RAG_MODE=off
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000
CMD ["python", "-m", "mcp_server.server"]
