"""
eval/plot_quadrant.py - วาดแผนภาพสี่ช่อง correctness กับ faithfulness

    python3 -m eval.plot_quadrant                       วาดทุกไฟล์ผลที่มี
    python3 -m eval.plot_quadrant "ไม่มี RAG" "มี RAG"   เลือกเฉพาะรอบที่ต้องการ

หนึ่งจุดคือหนึ่งคำถาม สีฟ้าคือรอบที่มี RAG สีส้มคือรอบที่ไม่มี
จุดที่ซ้อนกันจะถูกขยับเล็กน้อยเพื่อให้เห็นว่ามีกี่จุด
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402

from tools.chart import _thai_font       # noqa: E402

HERE = Path(__file__).resolve().parent
BLUE, ORANGE = "#2a78d6", "#eb6834"
# ขาที่สาม ใช้การตัดคำไทยอย่างเดียว ยังไม่ถึงชั้นเวกเตอร์
GREEN = "#1baf7a"


def _load(labels: list[str] | None) -> list[dict]:
    runs = []
    for f in sorted(HERE.glob("results_*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        if labels and data["label"] not in labels:
            continue
        runs.append(data)
    return runs


def _color(label: str, index: int) -> str:
    """สีฟ้าคือมี RAG สีส้มคือไม่มี ส่วนสีเขียวคือขากลางที่ใช้ BM25 อย่างเดียว"""
    low = label.lower()
    if "bm25" in low:
        return GREEN
    if "ไม่มี" in label or "no" in low or "baseline" in low:
        return ORANGE
    if "rag" in low or "มี" in label:
        return BLUE
    return [BLUE, ORANGE, GREEN][index % 3]


def plot(runs: list[dict], out: Path) -> Path:
    font = _thai_font()
    if font:
        plt.rcParams["font.family"] = font

    fig, ax = plt.subplots(figsize=(7.2, 6.4), dpi=150)
    rng = random.Random(7)

    # ใช้พิกัดข้อมูลตรง ๆ ไม่ใช้สัดส่วนแกน เพราะ xlim ไม่ได้เริ่มที่ 0
    ax.fill_between([0.5, 1.08], 0.5, 1.08, color="#1baf7a", alpha=0.07, lw=0)
    ax.axvline(0.5, color="#c3c2b7", lw=1, ls="--")
    ax.axhline(0.5, color="#c3c2b7", lw=1, ls="--")

    for i, run in enumerate(runs):
        c = _color(run["label"], i)
        xs, ys = [], []
        for r in run["results"]:
            xs.append(r["correctness"] + rng.uniform(-0.012, 0.012))
            ys.append(r["faithfulness"] + rng.uniform(-0.012, 0.012))
        n = len(run["results"])
        mx = sum(r["correctness"] for r in run["results"]) / n
        my = sum(r["faithfulness"] for r in run["results"]) / n
        ax.scatter(xs, ys, s=55, color=c, alpha=0.55, edgecolors="none",
                   label="%s  (C=%.2f F=%.2f, n=%d)" % (run["label"], mx, my, n))
        ax.scatter([mx], [my], s=190, marker="X", color=c,
                   edgecolors="white", linewidths=1.4, zorder=5)

    ax.set_xlim(-0.04, 1.08)
    ax.set_ylim(-0.04, 1.08)
    ax.set_xlabel("correctness — คำตอบตรงกับเฉลยไหม")
    ax.set_ylabel("faithfulness — ตัวเลขมาจากข้อมูลที่ดึงมาไหม")
    ax.set_title("ผลประเมิน หนึ่งจุดคือหนึ่งคำถาม  (X ตัวใหญ่คือค่าเฉลี่ย)")
    ax.grid(alpha=0.18)
    ax.legend(loc="lower left", fontsize=9, framealpha=0.9)

    for x, y, t in [(0.25, 0.75, "ดึงข้อมูลผิด"), (0.75, 0.75, "เป้าหมาย"),
                    (0.25, 0.25, "พังทั้งสองทาง"), (0.75, 0.25, "บังเอิญถูก")]:
        ax.text(x, y, t, ha="center", va="center", fontsize=10.5,
                color="#898781", zorder=0)

    fig.tight_layout()
    fig.savefig(out)
    plt.close(fig)
    return out


if __name__ == "__main__":
    labels = sys.argv[1:] or None
    runs = _load(labels)
    if not runs:
        raise SystemExit("ไม่พบไฟล์ผล รัน python3 -m eval.run_eval ก่อน")
    out = plot(runs, HERE.parent / "charts" / "quadrant.png")
    print("รอบที่วาด:", ", ".join(r["label"] for r in runs))
    print("บันทึกที่:", out)
    if not _thai_font():
        print("หมายเหตุ: เครื่องนี้ไม่มีฟอนต์ไทย ตัวอักษรไทยในรูปจะเป็นสี่เหลี่ยม")
