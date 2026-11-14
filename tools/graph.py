"""
ชั้นกราฟ ตอบว่าอะไรเกี่ยวข้องกับอะไร โดยไม่ต้องให้ผู้ใช้รู้โครงสร้างตารางก่อน

ทำไมถึงต้องมี
    query_complaints ตอบได้เฉพาะคำถามที่รู้อยู่แล้วว่าจะกรองด้วยอะไร
    แต่คำถามอย่าง "ผู้ให้บริการรายนี้มีปัญหาอะไรเป็นพิเศษ"
    หรือ "เจ้าไหนมีลักษณะปัญหาคล้ายเจ้านี้" ต้องมองความสัมพันธ์ก่อน
    จึงจะรู้ว่าควรกรองด้วยอะไรในขั้นต่อไป

กราฟนี้สร้างจากฐานข้อมูลเดียวกันทั้งหมด ไม่ได้เอาความรู้นอกมาเติม
    node  คือค่าจริงในคอลัมน์ เช่น (provider, "บริษัท ทรู มูฟ เอช ...")
    edge  คือจำนวนเรื่องที่ค่าสองค่านี้ปรากฏอยู่ในแถวเดียวกัน

ตัวเลข lift สำคัญกว่าจำนวนดิบ
    เจ้าใหญ่ย่อมมีเรื่องร้องเรียนเยอะไปหมดทุกหมวด ดูจำนวนดิบจะเห็นแต่เจ้าใหญ่
    lift เทียบกับค่าเฉลี่ยของทั้งระบบ จึงบอกได้ว่า "เรื่องนี้เยอะผิดปกติสำหรับเจ้านี้"
    lift = 1 คือพอ ๆ กับค่าเฉลี่ย มากกว่า 1 คือเด่นกว่าปกติ
"""

from __future__ import annotations

import threading

from . import db

# คู่คอลัมน์ที่สร้างความสัมพันธ์ เลือกเฉพาะคู่ที่ตีความได้จริง
# cat_level1 มีค่าเดียวและ cat_level2 มีสองค่า จึงไม่เอามา ไม่ให้สารสนเทศอะไร
EDGE_PAIRS = (
    ("provider", "problem_group"),
    ("provider", "service_type"),
    ("provider", "channel"),
    ("service_type", "problem_group"),
    ("problem_group", "problem_detail"),
    ("province", "problem_group"),
    ("province", "provider"),
    ("region", "province"),
)

MIN_ROWS = 5        # ความสัมพันธ์ที่บางกว่านี้เป็นเศษข้อมูล ไม่เอามาเสนอ
TOP_K = 8

_graph = None
_lock = threading.Lock()


def _build():
    """อ่านฐานข้อมูลครั้งเดียวแล้วสร้างกราฟเก็บไว้ในหน่วยความจำ"""
    import networkx as nx

    g = nx.Graph()
    con = db.connect()
    try:
        for a, b in EDGE_PAIRS:
            sql = (
                "select %s as a, %s as b, count(*) as n from complaints "
                "where %s is not null and %s <> '' and %s is not null and %s <> '' "
                "group by a, b" % (a, b, a, b, b, a)
            )
            for row in con.execute(sql):
                if row["n"] < MIN_ROWS:
                    continue
                na, nb = (a, row["a"]), (b, row["b"])
                g.add_node(na, field=a, value=row["a"])
                g.add_node(nb, field=b, value=row["b"])
                g.add_edge(na, nb, n=int(row["n"]))
    finally:
        con.close()
    print("[graph] สร้างกราฟแล้ว %d node %d edge" % (g.number_of_nodes(), g.number_of_edges()),
          flush=True)
    return g


def graph():
    global _graph
    if _graph is None:
        with _lock:
            if _graph is None:
                _graph = _build()
    return _graph


def _find_node(value: str, field: str | None = None):
    g = graph()
    want = str(value).strip()
    hits = [n for n in g.nodes if n[1] == want and (field is None or n[0] == field)]
    return hits[0] if hits else None


def _profile(g, node, to_field: str) -> dict[str, int]:
    """การกระจายของ node หนึ่งไปยังคอลัมน์ปลายทาง ใช้ทั้งหา lift และหาความคล้าย"""
    out = {}
    for nb in g.neighbors(node):
        if nb[0] == to_field:
            out[nb[1]] = g[node][nb]["n"]
    return out


def explore_relations(value: str, field: str | None = None,
                      to_field: str | None = None,
                      mode: str = "related", top_k: int = TOP_K) -> dict:
    """
    ดูว่าค่าหนึ่งเกี่ยวข้องกับอะไรบ้างในข้อมูลจริง

    value    : ค่าจริงในฐานข้อมูล ต้องผ่าน resolve_filter_values มาก่อน
    field    : คอลัมน์ของค่านั้น ระบุได้เพื่อกันชื่อซ้ำข้ามคอลัมน์
    to_field : อยากดูความสัมพันธ์ไปยังคอลัมน์ไหน เว้นไว้ได้จะคืนทุกคอลัมน์ที่เชื่อมถึง
    mode     : related  คืออะไรปรากฏร่วมกับค่านี้บ่อย
               similar  คือค่าอื่นในคอลัมน์เดียวกันที่มีรูปแบบปัญหาคล้ายกัน
    """
    g = graph()
    node = _find_node(value, field)
    if node is None:
        near = sorted({n[1] for n in g.nodes
                       if str(value).strip() and str(value).strip() in n[1]})[:10]
        return {
            "ok": False,
            "error": "ไม่มีค่า %r อยู่ในกราฟ" % value,
            "how_to_fix": "ต้องเรียก resolve_filter_values ให้ได้ค่าจริงก่อน "
                          "แล้วส่งค่าที่ได้มาทั้งสตริง ห้ามพิมพ์ย่อหรือดัดแปลง",
            "did_you_mean": near,
        }

    fields_out = [to_field] if to_field else sorted({n[0] for n in g.neighbors(node)})

    if mode == "similar":
        # เทียบว่าค่าอื่นในคอลัมน์เดียวกัน กระจายไปยังคอลัมน์ปลายทางคล้ายกันแค่ไหน
        # ใช้ cosine บนสัดส่วน ไม่ใช่จำนวนดิบ เจ้าเล็กกับเจ้าใหญ่จึงเทียบกันได้
        target = to_field or "problem_group"
        base = _profile(g, node, target)
        if not base:
            return {"ok": False,
                    "error": "ค่านี้ไม่มีความสัมพันธ์กับคอลัมน์ %s" % target,
                    "how_to_fix": "ลองเปลี่ยน to_field เป็นคอลัมน์อื่น หรือใช้ mode related"}
        bt = sum(base.values()) or 1
        bvec = {k: v / bt for k, v in base.items()}
        out = []
        for other in g.nodes:
            if other[0] != node[0] or other == node:
                continue
            prof = _profile(g, other, target)
            if not prof:
                continue
            t = sum(prof.values()) or 1
            ovec = {k: v / t for k, v in prof.items()}
            dot = sum(bvec.get(k, 0.0) * ovec.get(k, 0.0) for k in set(bvec) | set(ovec))
            nb_ = sum(v * v for v in bvec.values()) ** 0.5
            no_ = sum(v * v for v in ovec.values()) ** 0.5
            if nb_ and no_:
                out.append({"value": other[1], "field": other[0],
                            "similarity": round(dot / (nb_ * no_), 4),
                            "rows_all_years": t})
        out.sort(key=lambda d: -d["similarity"])
        return {
            "ok": True, "value": node[1], "field": node[0], "mode": "similar",
            "compared_on": target, "similar": out[:top_k],
            "note": "ความคล้ายวัดจากสัดส่วนของ %s ที่พบร่วมกัน ไม่ใช่ขนาดขององค์กร "
                    "ตัวเลข rows เป็นยอดรวมทุกปี ห้ามเอาไปตอบตรง ๆ "
                    "ถ้าผู้ใช้ถามตัวเลข ต้องเรียก query_complaints เสมอ" % target,
        }

    # mode related
    groups = {}
    for f in fields_out:
        prof = _profile(g, node, f)
        if not prof:
            continue
        total_from_node = sum(prof.values()) or 1

        # ฐานเปรียบเทียบของทั้งระบบ ใช้หาว่าเรื่องไหนเด่นผิดปกติสำหรับค่านี้
        field_total = {}
        for other in g.nodes:
            if other[0] != node[0]:
                continue
            for k, v in _profile(g, other, f).items():
                field_total[k] = field_total.get(k, 0) + v
        grand = sum(field_total.values()) or 1

        items = []
        for k, v in prof.items():
            share = v / total_from_node
            base = (field_total.get(k, 0) / grand) or 1e-9
            items.append({
                "value": k,
                "rows_all_years": v,
                "share_pct": round(share * 100, 2),
                "lift": round(share / base, 2),
            })
        items.sort(key=lambda d: -d["rows_all_years"])
        groups[f] = items[:top_k]

    if not groups:
        return {"ok": False,
                "error": "ค่า %r ไม่มีความสัมพันธ์ที่หนาพอในกราฟ" % value,
                "how_to_fix": "ลองใช้ query_complaints ตรง ๆ แทน"}

    return {
        "ok": True, "value": node[1], "field": node[0], "mode": "related",
        "related": groups,
        "note": "share_pct คือสัดส่วนภายในค่านี้ ส่วน lift เทียบกับค่าเฉลี่ยทั้งระบบ "
                "lift มากกว่า 1 แปลว่าเด่นผิดปกติสำหรับค่านี้ เท่ากับ 1 คือพอ ๆ กับทั่วไป "
                "ตัวเลขทั้งหมดเป็นยอดรวมทุกปีไว้ให้เลือกทิศทางเท่านั้น "
                "ถ้าผู้ใช้ถามตัวเลขหรือระบุปี ต้องเรียก query_complaints เสมอ ห้ามตอบจากตรงนี้",
    }


if __name__ == "__main__":
    import json

    g = graph()
    print("node", g.number_of_nodes(), "edge", g.number_of_edges())
    o = explore_relations("บริษัท ทรู มูฟ เอช ยูนิเวอร์แซล คอมมิวนิเคชั่น จำกัด",
                          "provider", to_field="problem_group")
    print(json.dumps(o, ensure_ascii=False, indent=2)[:1200])
    o = explore_relations("บริษัท แอดวานซ์ ไวร์เลส เน็ทเวอร์ค จำกัด", "provider",
                          mode="similar")
    print(json.dumps(o, ensure_ascii=False, indent=2)[:900])
    print(json.dumps(explore_relations("ไม่มีอยู่จริง"), ensure_ascii=False))
