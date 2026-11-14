"""
tools/dashboard.py - รวมหลายกราฟเป็นหน้าเดียว

สร้างไฟล์ HTML ที่ฝังรูปไว้ในตัว เปิดได้เลยโดยไม่ต้องมีไฟล์อื่นประกอบ
ส่งต่อหรือแนบส่งได้ทันที

ทุกแผงต้องมาจากผลลัพธ์ของ query_complaints จริง
และ SQL ที่ใช้ถูกแสดงไว้ใต้กราฟทุกอัน เพื่อให้ตรวจที่มาของตัวเลขได้
"""

from __future__ import annotations

import base64
import os
from datetime import datetime
from html import escape
from pathlib import Path

from .chart import OUT_DIR, render_chart

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#faf9f7;color:#1a1a19;line-height:1.6;
 font-family:-apple-system,"Segoe UI","Noto Sans Thai","Sarabun",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;font-weight:600;margin:0 0 4px}
.sub{color:#6b6a66;font-size:13.5px;margin:0 0 28px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:18px}
.panel{background:#fff;border:1px solid #e3e1db;border-radius:12px;padding:16px 18px}
.panel h2{font-size:15px;font-weight:600;margin:0 0 10px}
.panel img{width:100%;border-radius:8px;display:block}
.sql{background:#f6f5f2;border:1px solid #e3e1db;border-radius:7px;padding:9px 11px;
 margin-top:10px;font-size:11.5px;overflow-x:auto;white-space:pre-wrap;
 font-family:ui-monospace,Menlo,monospace;color:#52514e}
.warn{background:#fdf6ea;border:1px solid #f0dcb8;border-radius:8px;
 padding:11px 14px;margin:18px 0 0;font-size:13.5px}
.note{font-size:13.5px;color:#52514e;margin:6px 0 0}
.foot{color:#6b6a66;font-size:12.5px;margin-top:28px;border-top:1px solid #e3e1db;padding-top:14px}
"""


def _b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def generate_dashboard(
    title: str,
    panels: list[dict],
    notes: list[str] | None = None,
    filename: str | None = None,
) -> dict:
    """
    title  : หัวข้อหน้า
    panels : แต่ละแผง {"rows": [...], "chart_type": "bar", "x": "...", "y": "...",
                       "title": "...", "sql": "...", "warnings": [...]}
    notes  : ข้อความสรุปใต้หัวข้อ
    """
    if not panels:
        return {"html_path": None, "warnings": ["ไม่มีแผงให้วาด"]}

    warnings: list[str] = []
    blocks: list[str] = []

    for i, p in enumerate(panels, 1):
        rows = p.get("rows") or []
        if not rows:
            warnings.append("แผงที่ %d ไม่มีข้อมูล ข้ามไป" % i)
            continue
        drawn = render_chart(
            rows, p.get("chart_type", "bar"), p["x"], p["y"],
            p.get("title", "แผงที่ %d" % i),
            filename="panel_%d_%s.png" % (i, abs(hash(p.get("title", ""))) % 10**6))
        if not drawn.get("image_path"):
            warnings.extend(drawn.get("warnings", []))
            continue
        warnings.extend(w for w in drawn.get("warnings", []) if w not in warnings)

        sql = p.get("sql") or ""
        blocks.append(
            '<div class="panel"><h2>%s</h2>'
            '<img src="data:image/png;base64,%s" alt="%s">%s%s</div>' % (
                escape(p.get("title", "แผงที่ %d" % i)),
                _b64(drawn["image_path"]),
                escape(p.get("title", "")),
                ('<div class="sql">%s</div>' % escape(sql)) if sql else "",
                "".join('<div class="warn">%s</div>' % escape(w)
                        for w in (p.get("warnings") or [])),
            ))

    if not blocks:
        return {"html_path": None, "warnings": warnings + ["ไม่มีแผงไหนวาดได้เลย"]}

    body = (
        '<div class="wrap"><h1>%s</h1>'
        '<p class="sub">ข้อมูลเปิด กสทช. สถิติเรื่องร้องเรียนในกิจการโทรคมนาคม '
        'ปี 2552 ถึง 2569 · สร้างเมื่อ %s</p>%s<div class="grid">%s</div>'
        '<div class="foot">ทุกตัวเลขมาจากการสืบค้นฐานข้อมูลแบบอ่านอย่างเดียว '
        'SQL ที่ใช้แสดงไว้ใต้กราฟแต่ละอัน</div></div>' % (
            escape(title),
            datetime.now().strftime("%d/%m/%Y %H:%M"),
            "".join('<p class="note">%s</p>' % escape(n) for n in (notes or [])),
            "".join(blocks),
        ))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = filename or ("dashboard_%s.html" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    path = OUT_DIR / name
    path.write_text(
        "<!DOCTYPE html><html lang='th'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>%s</title><style>%s</style></head><body>%s</body></html>"
        % (escape(title), CSS, body), encoding="utf-8")

    return {"html_path": str(path), "panels_drawn": len(blocks), "warnings": warnings}


if __name__ == "__main__":
    from .query import query_complaints as q

    a = q(group_by=["year"], limit=30)
    b = q(metric="share_pct", group_by=["service_type"], limit=5)
    c = q(group_by=["province"], filters={"year": [2564]}, limit=8)

    out = generate_dashboard(
        "ภาพรวมเรื่องร้องเรียนโทรคมนาคม",
        [
            {"rows": a["rows"], "chart_type": "line", "x": "year", "y": "count",
             "title": "จำนวนเรื่องร้องเรียนรายปี", "sql": a["sql_used"],
             "warnings": a["warnings"]},
            {"rows": b["rows"], "chart_type": "pie", "x": "service_type", "y": "count",
             "title": "สัดส่วนตามประเภทบริการ", "sql": b["sql_used"],
             "warnings": b["warnings"]},
            {"rows": c["rows"], "chart_type": "barh", "x": "province", "y": "count",
             "title": "8 จังหวัดที่ร้องเรียนมากที่สุด ปี 2564", "sql": c["sql_used"],
             "warnings": c["warnings"]},
        ],
        notes=["ตัวอย่างหน้าสรุปที่ประกอบจากสามคำถาม"])
    print(out["html_path"], "|", out["panels_drawn"], "แผง")
    for w in out["warnings"]:
        print("  เตือน:", w)
