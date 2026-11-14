"""
tools/chart.py - วาดกราฟจากผลลัพธ์ที่ query_complaints คืนมา

ฟอนต์ไทย
    matplotlib ไม่มีฟอนต์ไทยมาให้ ถ้าไม่มีฟอนต์ ตัวอักษรไทยจะกลายเป็นสี่เหลี่ยม
    ใน Docker ติดตั้งด้วย fonts-thai-tlwg (ดู mcp_server/Dockerfile)
    ถ้าหาไม่เจอ จะวาดให้แต่แจ้งเตือนไว้ ไม่เงียบ
"""

from __future__ import annotations

import os
import warnings as _warnings
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
# เตือนเรื่องฟอนต์ไว้ใน warnings ที่คืนกลับแล้ว ไม่ต้องพ่นซ้ำทุกตัวอักษร
_warnings.filterwarnings("ignore", message="Glyph .* missing from font")                       # ไม่เปิดหน้าต่าง เหมาะกับรันใน container
import matplotlib.pyplot as plt             # noqa: E402
from matplotlib import font_manager         # noqa: E402

OUT_DIR = Path(os.environ.get("CHART_DIR", Path(__file__).resolve().parent.parent / "charts"))

THAI_HINTS = ("loma", "garuda", "norasi", "kinnari", "sarabun",
              "noto sans thai", "thai", "tlwg", "ayuthaya", "thonburi")

CHART_TYPES = ("bar", "barh", "line", "pie")


def _thai_font() -> str | None:
    for f in font_manager.fontManager.ttflist:
        if any(h in f.name.lower() for h in THAI_HINTS):
            return f.name
    return None


def render_chart(
    rows: list[dict],
    chart_type: str,
    x: str,
    y: str,
    title: str,
    filename: str | None = None,
) -> dict:
    """
    rows       : ผลลัพธ์จาก query_complaints
    chart_type : bar | barh | line | pie
    x          : ชื่อคอลัมน์ที่เป็นแกนหมวดหมู่ เช่น "province"
    y          : ชื่อคอลัมน์ตัวเลข เช่น "count"
    """
    if chart_type not in CHART_TYPES:
        raise ValueError("chart_type ต้องเป็นหนึ่งใน %s" % ", ".join(CHART_TYPES))
    if not rows:
        return {"image_path": None, "warnings": ["ไม่มีข้อมูลให้วาด"]}
    for col in (x, y):
        if col not in rows[0]:
            raise ValueError("ไม่มีคอลัมน์ %r ในผลลัพธ์ มีแต่ %s" % (col, ", ".join(rows[0])))

    warnings: list[str] = []
    font = _thai_font()
    if font:
        plt.rcParams["font.family"] = font
    else:
        warnings.append("ไม่พบฟอนต์ไทยในเครื่องนี้ ตัวอักษรไทยในกราฟจะเป็นสี่เหลี่ยม")

    labels = [str(r[x]) for r in rows]
    values = [r[y] for r in rows]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    name = filename or ("chart_%s_%s.png" % (chart_type, abs(hash((title, tuple(labels)))) % 10**8))
    path = OUT_DIR / name

    fig, ax = plt.subplots(figsize=(9, 5.2), dpi=130)

    if chart_type == "bar":
        ax.bar(labels, values, color="#4C78A8")
        ax.set_ylabel(y)
        plt.xticks(rotation=35, ha="right")
    elif chart_type == "barh":
        ax.barh(labels[::-1], values[::-1], color="#4C78A8")
        ax.set_xlabel(y)
    elif chart_type == "line":
        ax.plot(labels, values, marker="o", color="#4C78A8")
        ax.set_ylabel(y)
        ax.grid(alpha=0.3)
        plt.xticks(rotation=35, ha="right")
    else:  # pie
        ax.pie(values, labels=labels, autopct="%1.1f%%", startangle=90)
        ax.axis("equal")
        if len(labels) > 7:
            warnings.append("ข้อมูลมี %d กลุ่ม มากเกินไปสำหรับกราฟวงกลม "
                            "ควรใช้ bar หรือ barh แทน" % len(labels))

    ax.set_title(title)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)

    return {"image_path": str(path), "rows_plotted": len(rows), "warnings": warnings}


if __name__ == "__main__":
    demo = [{"province": "กรุงเทพมหานคร", "count": 798},
            {"province": "นนทบุรี", "count": 172},
            {"province": "สมุทรปราการ", "count": 125},
            {"province": "ปทุมธานี", "count": 118},
            {"province": "ชลบุรี", "count": 93}]
    print("ฟอนต์ไทยที่เจอ:", _thai_font() or "ไม่มี")
    print(render_chart(demo, "bar", "province", "count",
                       "5 จังหวัดที่ร้องเรียนมากที่สุด ปี 2564"))
