"""
lawrag/tone_fix.py - กู้ฉบับที่ถูกตีตกเพราะ "วรรณยุกต์หาย ฟอนต์ไม่ให้รหัส"

ปัญหาคืออะไร
    PDF บางฉบับฝังฟอนต์ที่ไม่ให้รหัส Unicode กับกราฟิกของวรรณยุกต์
    pdftotext จึงคืนตัวอักษรมาครบ แต่เครื่องหมายบนหัวอักษรหายเกลี้ยง เช่น

        ขอ ๒ บรรดาประกาศ ขอบังคับ และคำสั่งอื่นใด ในสวนที่มีกำหนดไวแลวในประกาศนี้
        ที่ถูกคือ  ข้อ ๒ บรรดาประกาศ ข้อบังคับ และคำสั่งอื่นใด ในส่วนที่มีกำหนดไว้แล้วในประกาศนี้

    สังเกตว่าไม่ได้หายทุกตัว  "สั่ง" กับ "อื่น" ยังมีไม้เอกอยู่
    เพราะวรรณยุกต์ที่ซ้อนบนสระบน (ิ ี ึ ื ั ํ) ยังได้รหัส
    ที่หายคือตัวที่ต้องวางบนพยัญชนะตรง ๆ หรือบนสระล่าง (ุ ู)

ทำไมพจนานุกรมอย่างเดียวไม่พอ
    ตอนซ่อมสระอำที่หายทั้งตัว เราสร้างตารางจากพจนานุกรม pythainlp ได้ 3,363 คู่
    เพราะรูปที่พังส่วนใหญ่ไม่ใช่คำจริง  "กาหนด" ไม่ใช่คำ จึงซ่อมเป็น "กำหนด" ได้อย่างปลอดภัย
    แต่วรรณยุกต์ที่หายสร้างรูปที่เป็นคำจริงเต็มไปหมด
        ขอ/ข้อ  เขา/เข้า  ไม/ไม่/ไม้  นา/น่า/น้า  มา/ม้า  สวน/ส่วน  ไว/ไว้
    ตารางแบบนั้นจะตัดสินไม่ได้ว่าคำไหนควรเป็นอะไร ต้องดูบริบท จึงใช้ LLM อ่านบริบทแล้วเติมคืน

แล้วจะกันไม่ให้ LLM แก้เนื้อกฎหมายได้อย่างไร
    ไม่รับข้อความจาก LLM มาทั้งก้อน  รับมาเฉพาะ "เครื่องหมาย" เท่านั้น
        1. ตัดทั้งสองฝั่งเป็นลำดับของ (ตัวอักษรฐาน, เครื่องหมายที่ตามหลัง)
        2. จับคู่ลำดับตัวอักษรฐานด้วย difflib
        3. ช่วงที่ตรงกัน หยิบเครื่องหมายจากฝั่ง LLM มาใส่  ช่วงที่ไม่ตรง ใช้ของเดิมทั้งดุ้น
        4. ประกอบข้อความใหม่โดยใช้ตัวอักษรและช่องว่างจาก PDF ทุกตัว
    ต่อให้ LLM แต่งเติม ตัดคำ หรือสลับลำดับ ก็ไม่มีทางหลุดเข้ามาในไฟล์
    ส่วนที่ LLM เปลี่ยนได้มีอย่างเดียวคือวรรณยุกต์ ซึ่งคือสิ่งที่เราขอให้มันทำพอดี

ทำไมเปลี่ยนจากการเทียบเป๊ะมาเป็น difflib (แก้เมื่อ 2026-11-21)
    รุ่นแรกใช้กฎว่าถ้าลำดับตัวอักษรฐานไม่ตรงเป๊ะ ให้ทิ้งทั้งก้อน
    รันจริงแล้วตกเกือบทุกก้อน เพราะโมเดลเผลอตัดหัวกระดาษของราชกิจจาฯ ที่แทรกกลางเนื้อทิ้ง
    การทิ้งทั้งก้อนเพราะต่างกันไม่กี่ตัวนั้นเสียของ จึงเปลี่ยนมาจับคู่เป็นช่วง
    ช่วงไหนตรงก็ได้ประโยชน์ ช่วงไหนไม่ตรงก็ปล่อยไว้ตามเดิม ความปลอดภัยเท่าเดิมทุกประการ

ไฟล์เข้า : data/law/txt/*.txt.rejected
ไฟล์ออก : data/law/txt/*.txt  เฉพาะฉบับที่ขอให้ซ่อม  และ data/law/tone_fix_report.csv
          พร้อม data/law/tone_fix_cache.json ไว้รันซ้ำโดยไม่เสียเงินใหม่

ใช้อย่างไร
    python -m lawrag.tone_fix --files 30_protect --probe 1    ยิงก้อนเดียวแล้วกางให้ดู
    python -m lawrag.tone_fix --files 30_protect,36_keyword
    python -m lawrag.tone_fix --files 30_protect --dry-run     นับก้อนอย่างเดียว ไม่ยิง API
"""
from __future__ import annotations

import argparse
import csv
import difflib
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
TXT_DIR = os.path.join(LAW_DIR, "txt")
CACHE_PATH = os.path.join(LAW_DIR, "tone_fix_cache.json")
REPORT_PATH = os.path.join(LAW_DIR, "tone_fix_report.csv")

MODEL = os.environ.get("TONE_MODEL", "openai/gpt-4.1-mini")
RETRIES = 3
SEG_CHARS = int(os.environ.get("TONE_SEG_CHARS", "600"))
MAX_TOKENS = int(os.environ.get("TONE_MAX_TOKENS", "8000"))

# เครื่องหมายที่ฟอนต์พังทำหาย  ทั้งหมดเป็นตัวที่ลอยอยู่บนหัวอักษร
#   ็ ไม้ไต่คู้ · ่ ไม้เอก · ้ ไม้โท · ๊ ไม้ตรี · ๋ ไม้จัตวา · ์ ทัณฑฆาต · ๎ ยามักการ
# ไม่รวมสระบนอย่าง ิ ี ึ ื ั และนิคหิต ํ เพราะพวกนั้นไม่หาย
# จึงนับเป็น "ตัวอักษรฐาน" ที่ LLM ห้ามแตะ
DROPPED = "็่้๊๋์๎"

# ตัวอย่างสอนงาน หยิบจากตัวบทจริงของ 30_protect สองบรรทัดแรก
# ต้องเขียนสระอำเป็น ำ ตัวเดียว ให้ตรงกับข้อความที่ผ่าน normalize มาแล้ว
SHOT_IN = ("ขอ ๒ บรรดาประกาศ ขอบังคับ และคำสั่งอื่นใด ในสวนที่มีกำหนดไวแลวในประกาศนี้ "
           "หรือซึ่งขัดหรือแยงกับประกาศนี้ ใหใชประกาศนี้แทน")
SHOT_OUT = ("ข้อ ๒ บรรดาประกาศ ข้อบังคับ และคำสั่งอื่นใด ในส่วนที่มีกำหนดไว้แล้วในประกาศนี้ "
            "หรือซึ่งขัดหรือแย้งกับประกาศนี้ ให้ใช้ประกาศนี้แทน")

SYSTEM = (
    "คุณคือเครื่องมือซ่อมข้อความภาษาไทยที่สกัดจาก PDF ของประกาศราชการไทย\n"
    "ฟอนต์ใน PDF ทำให้วรรณยุกต์ ไม้ไต่คู้ และทัณฑฆาต หายไปเกือบหมด "
    "ข้อความที่ได้รับจึงอ่านดูเหมือนสะกดผิดทั้งหน้า เช่น ขอ ที่ควรเป็น ข้อ "
    "และ ใหใช ที่ควรเป็น ให้ใช้\n"
    "หน้าที่ของคุณคือเติมเครื่องหมายเหล่านั้นกลับคืนให้ถูกต้องตามบริบทของภาษากฎหมายไทย\n"
    "\n"
    "กฎเหล็ก\n"
    "1. ห้ามเพิ่ม ลบ หรือเปลี่ยนตัวอักษรใด ๆ ห้ามเปลี่ยนสระ ห้ามเปลี่ยนตัวเลขไทยเป็นเลขอารบิก\n"
    "2. คัดลอกทุกอย่างมาครบ รวมถึงหัวกระดาษของราชกิจจานุเบกษาที่แทรกกลางเนื้อ "
    "เช่น บรรทัดที่เขียนว่า หนา ๘ เลม ๑๒๓ ตอนพิเศษ ๙๙ ง ห้ามตัดทิ้งเด็ดขาด\n"
    "3. คงการขึ้นบรรทัดใหม่ไว้ตามเดิม\n"
    "4. แก้ได้อย่างเดียวคือเติม  ็ ่ ้ ๊ ๋ ์ ๎  ลงบนตัวอักษรที่ขาด\n"
    "5. ห้ามอธิบาย ห้ามใส่เครื่องหมายคำพูดครอบ ตอบกลับมาเป็นข้อความที่ซ่อมแล้วเท่านั้น\n"
    "\n"
    "ตัวอย่าง\n"
    "ข้อความที่ได้รับ:\n" + SHOT_IN + "\n"
    "ข้อความที่ต้องตอบกลับ:\n" + SHOT_OUT + "\n"
)

USER_TMPL = "ข้อความที่ได้รับ:\n{seg}\nข้อความที่ต้องตอบกลับ:"


# ส่วนที่ 1: แยกข้อความเป็นลำดับของคลัสเตอร์
# หน้าที่: ทำให้เทียบ "ตัวอักษรฐาน" ของสองข้อความได้โดยไม่สนใจเครื่องหมายที่หาย
# รับ: ข้อความ · คืน: list ของ (ตัวอักษรฐาน, เครื่องหมายที่ตามหลัง)
def clusters(t: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for ch in t:
        if ch in DROPPED:
            if out:
                out[-1] = (out[-1][0], out[-1][1] + ch)
        else:
            out.append((ch, ""))
    return out


def bases_nospace(cl: list[tuple[str, str]]) -> list[str]:
    return [c for c, _ in cl if not c.isspace()]


# ส่วนที่ 2: ประกอบข้อความใหม่จากต้นฉบับ บวกเครื่องหมายจากฝั่ง LLM
# หน้าที่: รับประกันว่าตัวอักษรและช่องว่างทุกตัวมาจาก PDF ไม่ใช่จากโมเดล
# วิธี: จับคู่ลำดับตัวอักษรฐานด้วย difflib แล้วโอนเครื่องหมายเฉพาะช่วงที่ตรงกัน
# รับ: คลัสเตอร์ต้นฉบับ · คลัสเตอร์จาก LLM
# คืน: (ข้อความที่ซ่อมแล้ว, เติมกี่ตัว, ขัดกันแล้วเลือกของ PDF กี่ตัว, สัดส่วนที่จับคู่ได้)
def merge(src: list[tuple[str, str]], llm: list[tuple[str, str]]):
    idx_src = [i for i, (c, _) in enumerate(src) if not c.isspace()]
    a = [src[i][0] for i in idx_src]
    b = [c for c, _ in llm if not c.isspace()]
    b_marks = [m for c, m in llm if not c.isspace()]

    new_marks: list[str] = [""] * len(a)
    matched = 0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag != "equal":
            continue
        matched += i2 - i1
        for k in range(i2 - i1):
            new_marks[i1 + k] = b_marks[j1 + k]

    pick = dict(zip(idx_src, new_marks))
    buf, added, conflict = [], 0, 0
    for i, (ch, src_mark) in enumerate(src):
        buf.append(ch)
        nm = pick.get(i, "")
        if src_mark and nm and src_mark != nm:
            buf.append(src_mark)          # ต้นฉบับมีอยู่แล้วและไม่ตรงกัน เชื่อ PDF
            conflict += 1
        elif nm:
            buf.append(nm)
            if not src_mark:
                added += 1
        else:
            buf.append(src_mark)
    cover = matched / max(len(a), 1)
    return "".join(buf), added, conflict, cover


# ส่วนที่ 3: ตัดข้อความเป็นก้อนตามขอบบรรทัด
# หน้าที่: ไม่ตัดกลางบรรทัด เพราะ chunker อ่านหัวบรรทัดเพื่อหา "ข้อ ๑"
def segments(t: str, limit: int) -> list[str]:
    lines = t.splitlines(keepends=True)
    out, cur, n = [], [], 0
    for ln in lines:
        thai = sum(1 for c in ln if "฀" <= c <= "๿")
        if cur and n + thai > limit:
            out.append("".join(cur)); cur, n = [], 0
        cur.append(ln); n += thai
    if cur:
        out.append("".join(cur))
    return out


# ส่วนที่ 4: ยิงหนึ่งก้อนไปหาโมเดล
# รับ: client · ข้อความก้อนหนึ่ง · คืน: (ข้อความดิบที่โมเดลตอบ, ข้อความเตือนถ้ามี)
def ask(client, seg: str) -> tuple[str, str]:
    last = "ไม่ได้ยิง"
    for attempt in range(RETRIES):
        try:
            r = client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "system", "content": SYSTEM},
                          {"role": "user", "content": USER_TMPL.format(seg=seg)}],
                temperature=0, max_tokens=MAX_TOKENS)
            if getattr(r, "choices", None):
                fin = getattr(r.choices[0], "finish_reason", "")
                got = r.choices[0].message.content or ""
                if fin and fin != "stop":
                    return got, "โมเดลหยุดเพราะ %s อาจตอบไม่ครบ" % fin
                return got, ""
            last = "ไม่มี choices"
        except Exception as e:
            last = "%s: %s" % (type(e).__name__, str(e)[:90])
        time.sleep(2 ** attempt)
    return "", last


# ส่วนที่ 5: โหมดส่อง ยิงก้อนเดียวแล้วกางให้ดูว่าโมเดลทำอะไรจริง ๆ
def probe(client, seg: str) -> int:
    print("=" * 72)
    print("ส่งเข้าไป %d ตัวอักษร  ตัวอักษรฐานไม่นับช่องว่าง %d ตัว"
          % (len(seg), len(bases_nospace(clusters(seg)))))
    print("-" * 72)
    print(seg[:500])
    print("-" * 72)
    got, warn = ask(client, seg)
    if warn:
        print("คำเตือน:", warn)
    print("โมเดลตอบกลับ %d ตัวอักษร  ตัวอักษรฐาน %d ตัว"
          % (len(got), len(bases_nospace(clusters(got)))))
    print("-" * 72)
    print(got[:500])
    print("-" * 72)
    if not got:
        return 1
    a = bases_nospace(clusters(seg))
    b = bases_nospace(clusters(got))
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    print("จุดที่ตัวอักษรฐานไม่ตรงกัน")
    shown = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal" or shown >= 8:
            continue
        print("  %-7s ต้นฉบับ %r  โมเดล %r"
              % (tag, "".join(a[i1:i2])[:40], "".join(b[j1:j2])[:40]))
        shown += 1
    txt, added, conflict, cover = merge(clusters(seg), clusters(got))
    print()
    print("จับคู่ตัวอักษรฐานได้ %.1f%%  เติมวรรณยุกต์ %d ตัว  ขัดกัน %d ตัว"
          % (cover * 100, added, conflict))
    print("ตัวอย่างผลลัพธ์หลังประกอบคืน")
    print(txt[:500])
    return 0


# ส่วนที่ 6: จุดเริ่มทำงาน
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", required=True,
                    help="ชื่อฐานของไฟล์ คั่นด้วยจุลภาค เช่น 30_protect,36_keyword")
    ap.add_argument("--dry-run", action="store_true", help="นับก้อนอย่างเดียว ไม่ยิง API")
    ap.add_argument("--probe", type=int, default=0,
                    help="ยิงก้อนที่ระบุก้อนเดียวแล้วกางให้ดู ไม่เขียนไฟล์")
    ap.add_argument("--fresh", action="store_true", help="ไม่ใช้แคช ยิงใหม่ทั้งหมด")
    ap.add_argument("--min-cover", type=float, default=0.50,
                    help="ถ้าจับคู่ตัวอักษรฐานได้น้อยกว่านี้ ไม่เอาผลของโมเดลก้อนนั้น")
    args = ap.parse_args()

    from lawrag.extract import normalize            # รวบ ◌ํ+า เป็น ำ และซ่อมสระอำที่หาย

    names = [n.strip() for n in args.files.split(",") if n.strip()]
    cache: dict = {}
    if os.path.exists(CACHE_PATH) and not args.fresh:
        cache = json.load(open(CACHE_PATH, encoding="utf-8"))

    client = None
    if not args.dry_run:
        from lawrag.contextualize import load_env   # ใช้ตัวอ่าน .env ตัวเดียวกัน
        load_env()
        key = os.environ.get("OPENROUTER_API_KEY", "")
        if not key:
            print("ไม่พบ OPENROUTER_API_KEY ใน .env", file=sys.stderr)
            return 2
        from openai import OpenAI
        client = OpenAI(api_key=key, base_url="https://openrouter.ai/api/v1")
        print("ใช้โมเดล %s  ก้อนละไม่เกิน %d อักษรไทย" % (MODEL, SEG_CHARS))

    report = []
    for name in names:
        src_path = os.path.join(TXT_DIR, name + ".txt.rejected")
        if not os.path.exists(src_path):
            print("ไม่พบ %s" % src_path, file=sys.stderr)
            continue

        # สำคัญ: ผ่าน normalize ก่อนตัดก้อน
        # ไฟล์ .rejected ถูกเขียนด้วย extract.py รุ่นก่อนที่ยังไม่รวบ ◌ํ+า เป็น ำ
        # ถ้าไม่รวบก่อน โมเดลจะตอบ ำ ตัวเดียวแล้วตัวอักษรฐานนับไม่ตรงทุกคำที่มีสระอำ
        raw = normalize(open(src_path, encoding="utf-8").read())
        segs = segments(raw, SEG_CHARS)
        thai = sum(1 for c in raw if "฀" <= c <= "๿")
        print("%s  อักษรไทย %d  ตัด %d ก้อน" % (name, thai, len(segs)), flush=True)

        if args.dry_run:
            report.append((name, thai, len(segs), 0, 0, 0, "ทดลอง"))
            continue
        if args.probe:
            i = max(1, min(args.probe, len(segs)))
            return probe(client, segs[i - 1])

        out_parts, ok, weak, added_tot, conf_tot = [], 0, 0, 0, 0
        for i, seg in enumerate(segs, 1):
            ck = "%s::v2::%d::%d" % (name, i, len(seg))
            got = cache.get(ck)
            warn = ""
            if got is None:
                got, warn = ask(client, seg)
            if not got:
                out_parts.append(seg); weak += 1
                print("  ก้อน %d/%d ยิงไม่สำเร็จ (%s)" % (i, len(segs), warn), flush=True)
                continue
            txt, a, c, cover = merge(clusters(seg), clusters(got))
            if cover < args.min_cover:
                out_parts.append(seg); weak += 1
                print("  ก้อน %d/%d จับคู่ได้แค่ %.0f%% ไม่เอา %s"
                      % (i, len(segs), cover * 100, warn), flush=True)
                continue
            out_parts.append(txt); ok += 1
            added_tot += a; conf_tot += c
            cache[ck] = got
            print("  ก้อน %d/%d จับคู่ %.0f%% เติม %d ตัว %s"
                  % (i, len(segs), cover * 100, a, warn), flush=True)
            json.dump(cache, open(CACHE_PATH, "w", encoding="utf-8"), ensure_ascii=False)

        fixed = normalize("".join(out_parts))
        from lawrag.extract import judge
        passed, why, nthai = judge(fixed)

        # เขียนเป็น .txt ก็ต่อเมื่อผ่านเกณฑ์เดียวกับสายหลักเท่านั้น
        # ถ้าไม่ผ่านแล้วยังเขียน .txt ไว้ chunker จะหยิบตัวบทที่ยังพังเข้าคลังทันที
        # รุ่นแรกพลาดตรงนี้ เขียนทับทั้งที่ซ่อมไม่สำเร็จ จึงกันไว้ให้ชัด
        if passed:
            dst = os.path.join(TXT_DIR, name + ".txt")
        else:
            dst = os.path.join(TXT_DIR, name + ".txt.attempt")
        open(dst, "w", encoding="utf-8").write(fixed)
        print("  เขียน %s  ผลตรวจ %s  เติมวรรณยุกต์รวม %d ตัว  ก้อนที่ไม่เอา %d"
              % (os.path.basename(dst), why, added_tot, weak), flush=True)
        if not passed:
            print("  ยังไม่ผ่านเกณฑ์ จึงไม่เขียนเป็น .txt  chunker จะไม่หยิบไฟล์นี้",
                  flush=True)
        report.append((name, nthai, len(segs), ok, weak, added_tot, why))

    if report:
        with open(REPORT_PATH, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["file", "thai_chars", "segments", "ok", "skipped",
                        "marks_added", "judge"])
            w.writerows(report)
        print("สรุปอยู่ที่ %s" % REPORT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
