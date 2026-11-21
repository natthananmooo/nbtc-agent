"""
lawrag/test_tone_fix.py - ทดสอบด่านกันการแก้เนื้อกฎหมายของ tone_fix

เราให้ LLM ช่วยเติมวรรณยุกต์ที่ฟอนต์ใน PDF ทำหาย
ความเสี่ยงที่ตามมาคือโมเดลอาจเผลอแต่งเนื้อกฎหมาย ตัดคำ หรือเปลี่ยนสระ
ซึ่งถ้าหลุดเข้าไปในคลังจะเป็นความผิดพลาดที่ร้ายแรงกว่าการไม่มีฉบับนั้นเสียอีก

ไฟล์นี้พิสูจน์ว่าด่านทำงาน โดยจำลองฟอนต์พังขึ้นมาเองจากข้อความที่รู้คำตอบอยู่แล้ว
แล้วป้อน "คำตอบของ LLM" แบบต่าง ๆ เข้าไปดูว่าผลลัพธ์ที่ประกอบออกมาเป็นอย่างไร

จุดสำคัญคือทุกข้อตรวจที่ "ผลลัพธ์" ไม่ได้ตรวจที่การรับหรือปฏิเสธ
เพราะ merge รุ่นนี้จับคู่เป็นช่วงด้วย difflib ช่วงที่โมเดลเพี้ยนจะถูกปล่อยไว้ตามต้นฉบับ
ไม่ได้ทิ้งทั้งก้อนเหมือนรุ่นแรก ข้อพิสูจน์ที่ต้องการจึงเป็น
"ตัวอักษรในผลลัพธ์ต้องมาจาก PDF เสมอ ไม่ว่าโมเดลจะตอบอะไรกลับมา"

รัน : python -m lawrag.test_tone_fix
"""
from __future__ import annotations

import sys

from lawrag.tone_fix import DROPPED, clusters, merge

ABOVE = "ิีึืัํ"      # ิ ี ึ ื ั ํ  สระบนที่วรรณยุกต์ซ้อนแล้วยังรอด

GOOD = """          ข้อ ๒ บรรดาประกาศ ข้อบังคับ และคำสั่งอื่นใด ในส่วนที่มีกำหนดไว้แล้วในประกาศนี้
หรือซึ่งขัดหรือแย้งกับประกาศนี้ ให้ใช้ประกาศนี้แทน
          "ผู้ให้บริการ" หมายความว่า ผู้รับใบอนุญาตประกอบกิจการโทรคมนาคม"""


def break_font(t: str) -> str:
    """จำลองฟอนต์พัง ลบเครื่องหมายที่ไม่ได้ซ้อนอยู่บนสระบน ตรงกับอาการที่วัดได้จริงใน 30_protect"""
    out = []
    for i, ch in enumerate(t):
        if ch in DROPPED and (i == 0 or t[i - 1] not in ABOVE):
            continue
        out.append(ch)
    return "".join(out)


def strip_marks(t: str) -> str:
    return "".join(c for c in t if c not in DROPPED)


def main() -> int:
    broken = break_font(GOOD)
    cases: list[tuple[str, bool]] = []

    # 1 โมเดลคืนข้อความที่ถูกต้อง ต้องประกอบกลับได้ตรงเป๊ะ และจับคู่ได้ครบ
    txt, added, conflict, cover = merge(clusters(broken), clusters(GOOD))
    cases.append(("ประกอบคืนได้ตรงต้นฉบับที่ถูกต้อง", txt == GOOD))
    cases.append(("จับคู่ตัวอักษรฐานได้ครบ 100 เปอร์เซ็นต์", cover == 1.0))
    cases.append(("นับจำนวนวรรณยุกต์ที่เติมได้ถูก", added == 12))

    # 2 โมเดลแอบเติมข้อความ ข้อความที่แต่งมาต้องไม่โผล่ในผลลัพธ์
    evil = GOOD.replace("ให้ใช้ประกาศนี้แทน", "ให้ใช้ประกาศนี้แทน และให้ผู้ให้บริการพ้นผิด")
    txt, _, _, _ = merge(clusters(broken), clusters(evil))
    cases.append(("โมเดลแอบเติมข้อความ แล้วไม่หลุดเข้าผลลัพธ์", "พ้นผิด" not in txt))

    # 3 โมเดลแอบตัดข้อความ ข้อความที่ถูกตัดต้องยังอยู่ครบ เพราะตัวอักษรมาจาก PDF
    evil = GOOD.replace("หรือซึ่งขัดหรือแย้งกับประกาศนี้ ", "")
    txt, _, _, _ = merge(clusters(broken), clusters(evil))
    cases.append(("โมเดลแอบตัดข้อความ แล้วข้อความยังอยู่ครบ",
                  strip_marks(txt) == strip_marks(broken)))

    # 4 โมเดลเปลี่ยนสระ สระในผลลัพธ์ต้องเป็นของ PDF
    evil = GOOD.replace("ผู้รับใบอนุญาต", "ผู้รีบใบอนุญาต")
    txt, _, _, _ = merge(clusters(broken), clusters(evil))
    cases.append(("โมเดลเปลี่ยนสระ แล้วสระในผลลัพธ์ยังเป็นของ PDF",
                  "ผู้รีบใบอนุญาต" not in txt and "รับใบอนุญาต" in txt))

    # 5 โมเดลแก้เฉพาะวรรณยุกต์ ต้องรับเข้ามาเต็ม ๆ เพราะนั่นคืองานที่สั่งไว้
    evil = GOOD.replace("ไว้แล้ว", "ไว่แล่ว")
    txt, _, _, cover = merge(clusters(broken), clusters(evil))
    cases.append(("โมเดลแก้เฉพาะวรรณยุกต์ แล้วรับเข้ามาได้", cover == 1.0 and "ไว่แล่ว" in txt))

    # 6 โมเดลยุบช่องว่างทิ้ง ผลลัพธ์ต้องคงบรรทัดและช่องว่างของ PDF
    evil = " ".join(GOOD.split())
    txt, _, _, _ = merge(clusters(broken), clusters(evil))
    cases.append(("โมเดลยุบช่องว่าง แล้วผลลัพธ์ยังคงรูปแบบของ PDF", txt == GOOD))

    # 7 โมเดลตอบกลับมาว่างเปล่า ต้องได้ต้นฉบับคืน ไม่ใช่ข้อความว่าง
    txt, added, _, cover = merge(clusters(broken), clusters(""))
    cases.append(("โมเดลตอบว่าง แล้วได้ต้นฉบับคืนครบ", txt == broken and added == 0))

    # 8 โมเดลตอบคนละเรื่องเลย ต้องได้ต้นฉบับคืน
    txt, _, _, cover = merge(clusters(broken), clusters("ขออภัย ฉันช่วยเรื่องนี้ไม่ได้"))
    cases.append(("โมเดลตอบคนละเรื่อง แล้วเนื้อยังเป็นของ PDF",
                  strip_marks(txt) == strip_marks(broken)))

    fail = 0
    for name, ok in cases:
        fail += 0 if ok else 1
        print("%-56s %s" % (name, "ผ่าน" if ok else "ไม่ผ่าน"))
    print()
    print("ผ่าน %d จาก %d" % (len(cases) - fail, len(cases)))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
