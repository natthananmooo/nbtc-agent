"""
ตรวจว่าต่อ Langfuse ได้จริงไหม และบอกสาเหตุเมื่อไม่ได้

ที่ต้องมีตัวนี้เพราะ log ฝั่งเราบอกแค่ว่ามีคีย์หรือไม่มี
ถ้าคีย์ผิด หรือชี้ภูมิภาคผิด จะเงียบ ไม่มีอะไรฟ้อง แล้วไปรู้อีกทีตอนเปิดหน้าเว็บแล้วว่าง

วิธีรัน  python -m tests.test_langfuse
"""

import os

from dotenv import load_dotenv

load_dotenv()


def mask(v: str | None) -> str:
    if not v:
        return "ไม่มีค่า"
    return "%s...%s (%d ตัวอักษร)" % (v[:8], v[-4:], len(v))


pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
sec = os.environ.get("LANGFUSE_SECRET_KEY")
url = os.environ.get("LANGFUSE_BASE_URL") or "https://cloud.langfuse.com"

print("ค่าที่อ่านได้จาก .env")
print("  LANGFUSE_PUBLIC_KEY  %s" % mask(pub))
print("  LANGFUSE_SECRET_KEY  %s" % mask(sec))
print("  LANGFUSE_BASE_URL    %s" % url)

if not (pub and sec):
    print("\nยังไม่มีคีย์ครบสองตัว หยุดตรงนี้")
    raise SystemExit(1)

if pub.startswith("sk-") or sec.startswith("pk-"):
    print("\nคีย์สลับที่กันอยู่ public ต้องขึ้นต้นด้วย pk- และ secret ต้องขึ้นต้นด้วย sk-")

from langfuse import Langfuse  # noqa: E402

client = Langfuse(public_key=pub, secret_key=sec, base_url=url, debug=False)

print("\nทดสอบยืนยันตัวตนกับเซิร์ฟเวอร์")
try:
    ok = client.auth_check()
    print("  auth_check -> %s" % ok)
except Exception as e:
    print("  auth_check ล้ม: %s: %s" % (type(e).__name__, str(e)[:200]))
    print("\nสาเหตุที่พบบ่อย")
    print("  1 คีย์ผิดหรือถูกลบไปแล้ว")
    print("  2 คีย์เป็นของคนละภูมิภาค ลองเปลี่ยน LANGFUSE_BASE_URL")
    print("    EU  https://cloud.langfuse.com")
    print("    US  https://us.cloud.langfuse.com")
    raise SystemExit(1)

print("\nส่ง trace ทดสอบหนึ่งอัน")
with client.start_as_current_observation(
    name="ตรวจการเชื่อมต่อ", as_type="agent",
    input={"question": "นี่คือ trace ทดสอบ ไม่ใช่คำถามจริง"},
) as root:
    tid = client.get_current_trace_id()
    with client.start_as_current_observation(name="tool ทดสอบ", as_type="tool",
                                             input={"arg": 1}) as span:
        span.update(output={"ok": True})
    setter = getattr(client, "set_current_trace_io", None)
    if setter:
        setter(input="ทดสอบ", output="สำเร็จ")
    else:
        print("  (langfuse เวอร์ชันนี้ไม่มี set_current_trace_io ข้ามไป)")

client.create_score(name="correctness", value=1.0, trace_id=tid, data_type="NUMERIC")
client.flush()

print("  trace_id = %s" % tid)
print("\nส่งแล้ว เปิด Tracing ในหน้าเว็บ Langfuse ภายในไม่กี่วินาทีควรเห็น")
print("ถ้ายังว่าง แปลว่าคีย์เป็นของ project อื่น ให้ดูว่าหน้าเว็บเลือก project ตรงกับคีย์ไหม")
