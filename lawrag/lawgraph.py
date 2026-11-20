"""
lawrag/lawgraph.py - สร้างกราฟความรู้จากการอ้างอิงในตัวบทกฎหมาย

ทำไมต้องมีกราฟ ทั้งที่มี vector แล้ว
    กฎหมายไม่พูดซ้ำ แต่ชี้ไปหากัน ข้อห้ามอยู่ฉบับหนึ่ง บทลงโทษอยู่อีกฉบับ
    มาตราที่ว่าด้วยค่าปรับไม่มีคำว่า "ค่าบริการ" หรือ "เอาเปรียบ" อยู่เลยสักคำ
    เวกเตอร์ของคำถามกับเวกเตอร์ของบทลงโทษจึงอยู่คนละมุม ทั้งที่เป็นเรื่องเดียวกัน
    สิ่งที่ผูกสองที่นี้เข้าด้วยกันไม่ใช่ความคล้ายของถ้อยคำ แต่เป็นการอ้างอิง
    กราฟเก็บการอ้างอิงนั้นไว้เป็นเส้น แล้วให้ระบบเดินตามเส้นแทนที่จะเดาเอา

ปัญหาที่ต้องแก้ตอนสร้างเส้น
    ตัวบทเขียนว่า "อาศัยอำนาจตามความในมาตรา ๒๗ ... และมาตรา ๕๗ (๔) ประกอบกับ
    มาตรา ๓๑ และมาตรา ๔๐ แห่งพระราชบัญญัติองค์กรจัดสรรคลื่นความถี่ฯ พ.ศ. ๒๕๕๓
    ประกอบกับมาตรา ๔๕ มาตรา ๔๖ และมาตรา ๕๐ แห่งพระราชบัญญัติการประกอบกิจการ
    โทรคมนาคม พ.ศ. ๒๕๔๔"
    เลขมาตราถูกเขียนไว้ก่อน แล้วค่อยบอกว่าเป็นมาตราของ พ.ร.บ. ฉบับไหน
    ถ้าจับแค่ "มาตรา ๕๗" โดยไม่ดูว่าตามด้วย "แห่ง..." ฉบับไหน จะผูกเส้นผิดฉบับ
    จึงต้องกวาดจากซ้ายไปขวา เก็บเลขมาตราที่ค้างไว้ แล้วยกให้ พ.ร.บ. ตัวแรกที่เจอ

ไฟล์เข้า : data/law/chunks_contextual.json
ไฟล์ออก : data/law/law_graph.json  และ data/law/law_graph.cypher สำหรับ Neo4j
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter, defaultdict, deque

# ส่วนที่ 1: ค่าคงที่
BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
IN_PATH = os.path.join(LAW_DIR, "chunks_contextual.json")
OUT_JSON = os.path.join(LAW_DIR, "law_graph.json")
OUT_CYPHER = os.path.join(LAW_DIR, "law_graph.cypher")

THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙"


# หน้าที่: แปลงเลขไทยเป็นเลขอารบิก · รับ: ข้อความ · คืน: ข้อความที่เลขเป็นอารบิก
def th2ar(s: str) -> str:
    return "".join(str(THAI_DIGITS.index(c)) if c in THAI_DIGITS else c for c in s)


# ส่วนที่ 2: รูปแบบที่ใช้จับการอ้างอิง
# ชื่อ พ.ร.บ. ที่ตามหลังคำว่า แห่ง จบที่ พ.ศ. กับเลขสี่หลัก
RE_ACT_REF = re.compile(r"แห่ง(พระราชบัญญัติ.{0,140}?พ\.ศ\.\s*[๐-๙\d]{4})")
RE_MATRA = re.compile(r"มาตรา\s+([๐-๙\d]+)")
RE_KHOR = re.compile(r"ข้อ\s+([๐-๙\d]+)")
# ให้ยกเลิกประกาศ ... เรื่อง ...  ใช้ผูกเส้นว่าฉบับใหม่ยกเลิกฉบับเก่า
RE_REPEAL = re.compile(r"ให้ยกเลิก(.{0,200}?)(?:ข้อ\s|$)", re.S)
# นิยามศัพท์ "คำ" หมายความว่า
RE_DEFINE = re.compile(r"[“\"]([^”\"]{2,40})[”\"]\s*หมายความว่า")

# ชื่อ พ.ร.บ. ยาวและเขียนไม่เหมือนกันทุกที่ ย่อให้เหลือแก่นเพื่อให้ node ไม่แตก
ACT_ALIASES = (
    ("องค์กรจัดสรรคลื่นความถี่", "พ.ร.บ. องค์กรจัดสรรคลื่นความถี่ฯ"),
    ("การประกอบกิจการโทรคมนาคม", "พ.ร.บ. การประกอบกิจการโทรคมนาคม"),
    ("การประกอบกิจการกระจายเสียง", "พ.ร.บ. การประกอบกิจการกระจายเสียงฯ"),
    ("วิธีปฏิบัติราชการทางปกครอง", "พ.ร.บ. วิธีปฏิบัติราชการทางปกครอง"),
    ("จัดตั้งศาลปกครอง", "พ.ร.บ. จัดตั้งศาลปกครองฯ"),
    ("วิทยุคมนาคม", "พ.ร.บ. วิทยุคมนาคม"),
    ("อำนวยความสะดวก", "พ.ร.บ. อำนวยความสะดวกฯ"),
    ("คุ้มครองข้อมูลส่วนบุคคล", "พ.ร.บ. คุ้มครองข้อมูลส่วนบุคคล"),
    ("ข้อมูลข่าวสารของราชการ", "พ.ร.บ. ข้อมูลข่าวสารของราชการ"),
)


# หน้าที่: ย่อชื่อ พ.ร.บ. ให้เป็นชื่อมาตรฐาน · รับ: ชื่อยาวจากตัวบท · คืน: ชื่อย่อ
def norm_act(name: str) -> str:
    flat = re.sub(r"\s+", "", name)
    for key, short in ACT_ALIASES:
        if re.sub(r"\s+", "", key) in flat:
            year = re.search(r"พ\.ศ\.\s*([๐-๙\d]{4})", name)
            return "%s พ.ศ. %s" % (short, th2ar(year.group(1)) if year else "?")
    return re.sub(r"\s+", " ", name).strip()[:70]


# ส่วนที่ 3: หาว่า "มาตรา N" ที่เจอ เป็นมาตราของ พ.ร.บ. ฉบับไหน
# หน้าที่: กวาดข้อความจากซ้ายไปขวา จับคู่เลขมาตราที่ค้างอยู่กับ พ.ร.บ. ตัวแรกที่ตามมา
# รับ: เนื้อความ และชื่อ พ.ร.บ. ที่ใช้เมื่อไม่มีตัวบอก
# คืน: รายการ (ชื่อ พ.ร.บ., เลขมาตรา) ที่ไม่ซ้ำ
def resolve_sections(text: str, default_act: str | None) -> list[tuple[str, int]]:
    events: list[tuple[int, str, str]] = []
    for m in RE_MATRA.finditer(text):
        events.append((m.start(), "matra", m.group(1)))
    for m in RE_ACT_REF.finditer(text):
        events.append((m.start(), "act", m.group(1)))
    events.sort()

    out: list[tuple[str, int]] = []
    pending: list[int] = []
    for _, kind, val in events:
        if kind == "matra":
            n = th2ar(val)
            if n.isdigit():
                pending.append(int(n))
        else:
            act = norm_act(val)
            out.extend((act, n) for n in pending)   # เลขที่ค้างอยู่เป็นของ พ.ร.บ. ตัวนี้
            pending = []
    if pending and default_act:                      # ไม่มีตัวบอก ถือว่าอ้างฉบับตัวเอง
        out.extend((default_act, n) for n in pending)
    return sorted(set(out))


# ส่วนที่ 4: สร้างกราฟ
# หน้าที่: อ่าน chunk ทั้งหมดแล้วประกอบเป็น node กับ edge
# รับ: รายการ chunk · คืน: (nodes, edges) เป็น dict และ list
def build(rows: list[dict]) -> tuple[dict, list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    # หน้าที่: เพิ่ม node ถ้ายังไม่มี · รับ: id, ชนิด, ข้อมูลเพิ่ม · คืน: id เดิม
    def add_node(nid: str, label: str, **kw) -> str:
        if nid not in nodes:
            nodes[nid] = {"id": nid, "label": label, **kw}
        else:
            nodes[nid].update({k: v for k, v in kw.items() if v})
        return nid

    # หน้าที่: เพิ่มเส้น · รับ: ต้นทาง ปลายทาง ชนิด · คืน: ไม่คืนค่า
    def add_edge(src: str, dst: str, rel: str) -> None:
        edges.append({"source": src, "target": dst, "relationship": rel})

    by_doc: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_doc[r["doc_file"]].append(r)

    # 4.1 node ของเอกสารแต่ละฉบับ และหา พ.ร.บ. ที่ให้อำนาจจากอารัมภบท
    doc_main_act: dict[str, str | None] = {}
    for f, cs in by_doc.items():
        d = cs[0]
        did = "doc::" + f.replace(".pdf", "")
        add_node(did, "Document", title=d["doc_title"], law_type=d["law_type"],
                 category=d["category"], url=d["url"], n_chunks=len(cs))
        pre = " ".join(c["body"] for c in cs if c["unit"] == "อารัมภบท")
        acts = [norm_act(m.group(1)) for m in RE_ACT_REF.finditer(pre)]
        doc_main_act[f] = acts[0] if acts else None
        for act, n in resolve_sections(pre, None):
            aid = add_node("act::" + act, "Act", name=act)
            sid = add_node("sec::%s::%d" % (act, n), "ActSection", act=act, number=n)
            add_edge(sid, aid, "SECTION_OF")
            add_edge(did, sid, "AUTHORIZED_BY")   # ประกาศฉบับนี้ออกโดยอาศัยมาตรานี้

    # 4.2 node ของ chunk และเส้นที่ออกจาก chunk
    for r in rows:
        f = r["doc_file"]
        did = "doc::" + f.replace(".pdf", "")
        cid = "chunk::" + r["id"]
        add_node(cid, "Chunk", doc=f, unit=r["unit"], number=r["number"],
                 muad=r["muad"], title=r["doc_title"],
                 preview=r["body"][:140], n_chars=r["n_chars"])
        add_edge(did, cid, "CONTAINS")

        # ตัวบทของ พ.ร.บ. อ้างมาตราในฉบับตัวเอง ถ้าไม่ได้บอกว่าแห่งฉบับอื่น
        default_act = doc_main_act.get(f) if r["unit"] == "มาตรา" else None
        if r["unit"] == "มาตรา" and f.endswith("_act.pdf"):
            default_act = norm_act(r["doc_title"])
            add_node("act::" + default_act, "Act", name=default_act)
            own = add_node("sec::%s::%d" % (default_act, r["number"]),
                           "ActSection", act=default_act, number=r["number"])
            add_edge(own, cid, "TEXT_OF")        # มาตรานี้มีตัวบทอยู่ที่ chunk นี้

        for act, n in resolve_sections(r["body"], default_act):
            aid = add_node("act::" + act, "Act", name=act)
            sid = add_node("sec::%s::%d" % (act, n), "ActSection", act=act, number=n)
            add_edge(sid, aid, "SECTION_OF")
            add_edge(cid, sid, "CITES_SECTION")

        # อ้างข้ออื่นในฉบับเดียวกัน ผูกตรงไปยัง chunk ของข้อนั้น
        for n in r["cites_khor"]:
            tgt = next((x for x in by_doc[f]
                        if x["unit"] == "ข้อ" and x["number"] == n), None)
            if tgt and tgt["id"] != r["id"]:
                add_edge(cid, "chunk::" + tgt["id"], "CITES_CLAUSE")

        # นิยามศัพท์ เป็น node ของตัวเอง เพราะเป็นสิ่งที่คนค้นหาบ่อย
        for term in RE_DEFINE.findall(r["body"]):
            tid = add_node("term::" + term.strip(), "Definition", term=term.strip())
            add_edge(cid, tid, "DEFINES")

        # ยกเลิกประกาศฉบับเก่า ถ้าชื่อตรงกับฉบับที่เรามี ผูกเส้นให้
        if "ให้ยกเลิก" in r["body"]:
            for m in RE_REPEAL.finditer(r["body"]):
                frag = re.sub(r"\s+", "", m.group(1))
                for f2, cs2 in by_doc.items():
                    if f2 == f:
                        continue
                    key = re.sub(r"\s+", "", cs2[0]["doc_title"])[:40]
                    if key and key in frag:
                        add_edge(did, "doc::" + f2.replace(".pdf", ""), "REPEALS")

    # 4.3 มาตราไหนมีตัวบทอยู่ในคลังบ้าง ผูกให้ครบ เพื่อให้เดินจากการอ้างอิงไปถึงตัวบทได้
    for r in rows:
        if r["unit"] != "มาตรา" or not r["doc_file"].endswith("_act.pdf"):
            continue
        act = norm_act(r["doc_title"])
        sid = "sec::%s::%d" % (act, r["number"])
        if sid in nodes:
            add_edge(sid, "chunk::" + r["id"], "TEXT_OF")

    # ตัดเส้นซ้ำ กราฟเดินเร็วขึ้นและนับจำนวนได้ตรง
    seen = set()
    uniq = []
    for e in edges:
        k = (e["source"], e["target"], e["relationship"])
        if k not in seen:
            seen.add(k)
            uniq.append(e)
    return nodes, uniq


# ส่วนที่ 5: กติกาการเดินกราฟ
# ทำไมต้องกำหนดทิศทาง ไม่เดินทุกเส้นสองทางแบบธรรมดา
#     node เอกสารเชื่อมกับทุก chunk ของตัวเอง ฉบับใหญ่สุดมี 363 เส้น
#     ถ้าเดินสองทางแบบไม่มีกติกา พอแตะ node เอกสารปุ๊บจะกวาดทั้งฉบับเข้ามา
#     วัดจริงจากข้อ ๑๗ เดิน 2 ก้าวแบบไม่มีกติกา ได้ 37 node ซึ่ง 29 node
#     เป็น chunk ที่ไม่เกี่ยวกันเลย เป็นแค่เพื่อนร่วมฉบับ
#     กติกานี้ให้เดินจาก chunk ขึ้นไปหาเอกสารได้ เพื่อรู้ว่าอยู่ฉบับไหน
#     แต่ห้ามเดินจากเอกสารลงไปหา chunk อื่น
#
# เส้นที่มีค่าที่สุดคือ CITES_SECTION ทิศย้อนกลับ
#     จากมาตราหนึ่ง เดินย้อนไปหาทุกข้อในทุกฉบับที่อ้างมาตรานั้น
#     นี่คือเส้นที่พา "ข้อห้าม" ไปเจอ "บทลงโทษ" ซึ่งอยู่คนละฉบับและไม่มีคำร่วมกัน
TRAVERSE_RULES: dict[str, list[tuple[str, str]]] = {
    "Chunk": [("out", "CITES_SECTION"), ("out", "CITES_CLAUSE"),
              ("out", "DEFINES"), ("in", "CONTAINS"), ("in", "TEXT_OF")],
    "Document": [("out", "AUTHORIZED_BY"), ("out", "REPEALS")],
    "ActSection": [("out", "TEXT_OF"), ("in", "CITES_SECTION"), ("in", "AUTHORIZED_BY")],
    "Definition": [("in", "DEFINES")],
    "Act": [],
}


# หน้าที่: เดินกราฟจาก node ตั้งต้นตามกติกาข้างบน
# รับ: nodes, edges, รายการ node ตั้งต้น, จำนวนก้าว, เพดานจำนวน node
# คืน: รายการ (ก้าวที่, ชนิดเส้น, node ที่ไปถึง) เรียงตามลำดับที่เจอ
def traverse(nodes: dict, edges: list[dict], seeds: list[str],
             hops: int = 2, cap: int = 40) -> list[tuple[int, str, str]]:
    out_e: dict[str, list] = defaultdict(list)
    in_e: dict[str, list] = defaultdict(list)
    for e in edges:
        out_e[e["source"]].append((e["target"], e["relationship"]))
        in_e[e["target"]].append((e["source"], e["relationship"]))

    seen = set(seeds)
    q = deque((s, 0) for s in seeds)
    found: list[tuple[int, str, str]] = []
    while q:
        cur, h = q.popleft()
        if h >= hops:
            continue
        for direction, rel in TRAVERSE_RULES.get(nodes.get(cur, {}).get("label", ""), []):
            for nb, r in (out_e[cur] if direction == "out" else in_e[cur]):
                if r != rel or nb in seen:
                    continue
                seen.add(nb)
                found.append((h + 1, rel, nb))
                q.append((nb, h + 1))
                if len(seen) > cap:
                    return found
    return found


# ส่วนที่ 5: ตรวจกราฟจากข้อมูลจริง ไม่ใช่จากที่เราคิดว่าควรเป็น
# หน้าที่: รายงานสถิติและทดลองเดินจากข้อที่สำคัญ เพื่อดูว่าเส้นใช้ได้จริง
def report(nodes: dict, edges: list[dict]) -> None:
    print("node %d · edge %d\n" % (len(nodes), len(edges)))
    print("ชนิดของ node")
    for k, v in Counter(n["label"] for n in nodes.values()).most_common():
        print("  %-14s %5d" % (k, v))
    print("\nชนิดของเส้น")
    for k, v in Counter(e["relationship"] for e in edges).most_common():
        print("  %-18s %5d" % (k, v))

    deg = Counter()
    for e in edges:
        deg[e["source"]] += 1
        deg[e["target"]] += 1
    print("\nnode ที่มีเส้นเชื่อมมากที่สุด")
    for nid, d in deg.most_common(8):
        n = nodes.get(nid, {})
        name = n.get("name") or n.get("term") or n.get("title", "")
        extra = "" if n.get("label") != "ActSection" else " มาตรา %s" % n.get("number")
        print("  %3d เส้น  [%s]%s %s" % (d, n.get("label", "?"), extra, str(name)[:46]))

    # เดินจริงจากข้อ ๑๗ ของประกาศเรื่องร้องเรียน เพื่อดูว่ากติกาการเดินใช้ได้จริง
    start = next((nid for nid, n in nodes.items()
                  if n.get("label") == "Chunk" and n.get("doc", "").startswith("32")
                  and n.get("number") == 17), None)
    if not start:
        return
    for hops in (2, 3):
        res = traverse(nodes, edges, [start], hops=hops)
        kinds = Counter(nodes.get(n, {}).get("label", "?") for _, _, n in res)
        print("\nเดิน %d ก้าวจากข้อ ๑๗ ของประกาศเรื่องร้องเรียน เจอ %d node %s"
              % (hops, len(res), dict(kinds)))
        if hops == 3:
            for h, rel, nb in res:
                n = nodes.get(nb, {})
                if n.get("label") != "Chunk" or h < 3:
                    continue
                print("    hop3 [%s] %s | %s" % (rel, str(n.get("title", ""))[:34],
                                                 str(n.get("preview", ""))[:60]))


# ส่วนที่ 6: จุดเริ่มทำงาน
def main() -> int:
    rows = json.load(open(IN_PATH, encoding="utf-8"))
    nodes, edges = build(rows)
    json.dump({"nodes": list(nodes.values()), "edges": edges},
              open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # เขียน Cypher ไว้ด้วย เผื่อวันหนึ่งย้ายไป Neo4j จะได้ไม่ต้องเขียนใหม่
    with open(OUT_CYPHER, "w", encoding="utf-8") as f:
        for n in nodes.values():
            props = ", ".join("%s: %s" % (k, json.dumps(v, ensure_ascii=False))
                              for k, v in n.items() if k != "label")
            f.write("CREATE (:%s {%s});\n" % (n["label"], props))
        for e in edges:
            f.write("MATCH (a {id: %s}), (b {id: %s}) CREATE (a)-[:%s]->(b);\n"
                    % (json.dumps(e["source"], ensure_ascii=False),
                       json.dumps(e["target"], ensure_ascii=False), e["relationship"]))

    report(nodes, edges)
    print("\nเขียน %s และ %s" % (OUT_JSON, OUT_CYPHER))
    return 0


if __name__ == "__main__":
    sys.exit(main())
