FROM python:3.11-slim

WORKDIR /app

COPY web/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY tools/ ./tools/
COPY web/ ./web/
COPY agent.py observability.py aliases.yaml ./

# คุยกับ MCP Server ด้วยชื่อ service ในเครือข่ายของ compose
ENV MCP_URL=http://mcp:8000/mcp
ENV PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8080"]
