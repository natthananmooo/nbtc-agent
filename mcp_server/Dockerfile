FROM python:3.11-slim

# fonts-thai-tlwg ทำให้ matplotlib วาดตัวอักษรไทยได้ ไม่เป็นสี่เหลี่ยม
RUN apt-get update \
 && apt-get install -y --no-install-recommends fonts-thai-tlwg \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY mcp_server/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY tools/ ./tools/
COPY mcp_server/ ./mcp_server/
COPY aliases.yaml ./aliases.yaml

# ฐานข้อมูลไม่ได้ copy เข้า image แต่ mount เข้ามาแบบอ่านอย่างเดียวตอนรัน
ENV COMPLAINTS_DB=/data/complaints.db
ENV CHART_DIR=/charts
ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000

EXPOSE 8000
CMD ["python", "-m", "mcp_server.server"]
