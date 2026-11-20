FROM python:3.11-slim

# fonts-thai-tlwg ไม่จำเป็นตรงนี้ แต่ poppler ใช้แกะ PDF ซ้ำได้ในคอนเทนเนอร์
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY lawrag/requirements.txt ./requirements.txt

# ลง torch รุ่น CPU จาก index ของ pytorch เอง เหตุผลเดียวกับ image ของ mcp
# ถ้าปล่อยให้ลงจาก pypi จะได้รุ่น CUDA ที่ใหญ่กว่าสิบเท่า ทั้งที่ไม่มีการ์ดจอให้ใช้
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements.txt

# ฝังโมเดลฝังเวกเตอร์ไว้ใน image ตั้งแต่ตอน build
# ตอนรันจึงไม่ต้องต่ออินเทอร์เน็ต และได้โมเดลเวอร์ชันเดิมทุกครั้งที่รัน ผลวัดจึงเทียบกันได้
# BAAI/bge-m3 เป็นตัวเดียวกับที่บทความของอาจารย์ใช้ รองรับไทยและให้เวกเตอร์ 1024 มิติ
ARG EMBED_MODEL=BAAI/bge-m3
ENV EMBED_MODEL=${EMBED_MODEL}
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('${EMBED_MODEL}')"

# โหลดพจนานุกรมตัดคำไทยของ pythainlp ไว้ล่วงหน้า กันไม่ให้ไปโหลดตอนรัน
RUN python -c "from pythainlp.tokenize import word_tokenize; word_tokenize('ทดสอบการตัดคำภาษาไทย', engine='newmm')"

ENV HF_HUB_OFFLINE=1

COPY lawrag/ ./lawrag/

ENV PGDSN=postgresql://law:law@pgvec:5432/law
ENV LAW_DIR=/data/law
ENV PYTHONUNBUFFERED=1

# ไม่มี server ให้รัน คอนเทนเนอร์นี้ใช้สั่งงานเป็นครั้ง ๆ ด้วย docker compose run
CMD ["python", "-m", "lawrag.index"]
