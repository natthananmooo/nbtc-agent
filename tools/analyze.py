"""
tools/analyze.py - สรุปตัวเลขจากผลลัพธ์ที่ query_complaints คืนมา

คำนวณด้วยโค้ด ไม่ใช่ให้ LLM คิดเลขเอง
แยก findings กับ caveats ออกจากกัน เพื่อให้ตอนประเมิน faithfulness
ตรวจได้ว่าระบบแจ้งข้อจำกัดครบหรือไม่
"""

from __future__ import annotations

MEASURES = ("avg_days_to_close", "share_pct", "count")
NON_DIM = {"count", "n", "share_pct", "avg_days_to_close"}

UNIT = {
    "count": "เรื่อง",
    "share_pct": "%",
    "avg_days_to_close": "วัน",
}


def _shape(rows: list[dict]):
    keys = list(rows[0])
    measure = next((m for m in MEASURES if m in keys), None)
    dims = [k for k in keys if k not in NON_DIM]
    return measure, dims


def _fmt(v, measure):
    return "%s %s" % (("{:,}".format(v) if isinstance(v, int) else v), UNIT.get(measure, ""))


def analyze_results(
    rows: list[dict],
    question: str = "",
    mode: str = "summary",
    warnings: list | None = None,
) -> dict:
    """
    rows     : ผลลัพธ์จาก query_complaints
    question : คำถามเดิมของผู้ใช้ ใช้บันทึกเป็น context
    mode     : summary | trend | compare | share
    warnings : warnings ที่ query_complaints คืนมา จะถูกนำมาใส่ caveats
    """
    caveats = list(warnings or [])

    if not rows:
        return {"question": question, "mode": mode, "findings": [],
                "caveats": caveats + ["ไม่มีข้อมูลที่ตรงเงื่อนไข อย่าสรุปตัวเลขใด ๆ"]}

    measure, dims = _shape(rows)
    if measure is None:
        return {"question": question, "mode": mode, "findings": [],
                "caveats": caveats + ["ไม่พบคอลัมน์ตัวเลขในผลลัพธ์"]}

    findings: list[str] = []
    dim = dims[0] if dims else None
    label = lambda r: " / ".join(str(r[d]) for d in dims) if dims else "ทั้งหมด"

    if not dims:
        findings.append("ค่ารวม %s" % _fmt(rows[0][measure], measure))
        return {"question": question, "mode": mode, "findings": findings, "caveats": caveats}

    total = sum(r[measure] for r in rows if isinstance(r[measure], (int, float)))

    if mode == "trend" and dim in ("year", "month"):
        ordered = sorted(rows, key=lambda r: r[dim])
        first, last = ordered[0], ordered[-1]
        peak = max(ordered, key=lambda r: r[measure])
        low = min(ordered, key=lambda r: r[measure])
        findings.append("ช่วง %s ถึง %s เปลี่ยนจาก %s เป็น %s"
                        % (first[dim], last[dim],
                           _fmt(first[measure], measure), _fmt(last[measure], measure)))
        if first[measure]:
            pct = (last[measure] - first[measure]) / first[measure] * 100
            findings.append("คิดเป็น %s %.1f%%" % ("เพิ่มขึ้น" if pct >= 0 else "ลดลง", abs(pct)))
        findings.append("สูงสุดที่ %s %s" % (peak[dim], _fmt(peak[measure], measure)))
        findings.append("ต่ำสุดที่ %s %s" % (low[dim], _fmt(low[measure], measure)))

    elif mode == "compare":
        ordered = sorted(rows, key=lambda r: r[measure], reverse=True)
        hi, lo = ordered[0], ordered[-1]
        findings.append("สูงสุดคือ %s ที่ %s" % (label(hi), _fmt(hi[measure], measure)))
        findings.append("ต่ำสุดคือ %s ที่ %s" % (label(lo), _fmt(lo[measure], measure)))
        if lo[measure]:
            findings.append("ต่างกัน %.1f เท่า" % (hi[measure] / lo[measure]))

    elif mode == "share":
        ordered = sorted(rows, key=lambda r: r[measure], reverse=True)
        for r in ordered[:3]:
            share = r.get("share_pct")
            if share is None and total:
                share = r[measure] / total * 100
            size = r.get("count", r[measure])
            findings.append("%s %s คิดเป็น %.1f%% ของผลลัพธ์"
                            % (label(r), _fmt(size, "count" if "count" in r else measure),
                               share or 0))
        if len(ordered) > 3 and total:
            top3 = sum(r[measure] for r in ordered[:3])
            findings.append("สามอันดับแรกรวมกัน %.1f%% ของผลลัพธ์" % (top3 / total * 100))

    else:  # summary
        ordered = sorted(rows, key=lambda r: r[measure], reverse=True)
        top = ordered[0]
        findings.append("มีทั้งหมด %d กลุ่ม" % len(rows))
        findings.append("อันดับหนึ่งคือ %s ที่ %s" % (label(top), _fmt(top[measure], measure)))
        if measure != "avg_days_to_close" and total:
            findings.append("คิดเป็น %.1f%% ของผลลัพธ์ที่แสดง" % (top[measure] / total * 100))
        if len(ordered) > 1:
            findings.append("รองลงมาคือ %s ที่ %s"
                            % (label(ordered[1]), _fmt(ordered[1][measure], measure)))

    if measure == "avg_days_to_close":
        caveats.append("ค่าเฉลี่ยไวต่อค่าสุดโต่ง ควรดูค่ามัธยฐานประกอบก่อนสรุป")

    return {"question": question, "mode": mode, "findings": findings, "caveats": caveats}


if __name__ == "__main__":
    from .query import query_complaints

    for title, kw, mode in [
        ("แนวโน้มรายปี", dict(group_by=["year"]), "trend"),
        ("5 จังหวัดมากสุด", dict(group_by=["province"], filters={"year": [2021]}, limit=5), "summary"),
        ("สัดส่วนประเภทบริการ", dict(metric="share_pct", group_by=["service_type"], limit=6), "share"),
        ("เวลาปิดเรื่องรายภูมิภาค", dict(metric="avg_days_to_close", group_by=["region"]), "compare"),
    ]:
        q = query_complaints(**kw)
        a = analyze_results(q["rows"], title, mode, q["warnings"])
        print("\n=== %s (%s) ===" % (title, mode))
        for f in a["findings"]:
            print("  พบ   :", f)
        for c in a["caveats"]:
            print("  ข้อจำกัด:", c)
