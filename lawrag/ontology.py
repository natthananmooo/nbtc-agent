"""
lawrag/ontology.py - อ่าน ontology.ttl แล้วเอาไปตรวจกราฟจริง กับอนุมานเส้นใหม่

ทำไมต้องมีไฟล์นี้ ไม่ใช่แค่วาดแผนผังไว้ใน README
    แผนผังใน README ไม่มีใครบังคับให้จริง วันหนึ่งมีคนเพิ่มเส้นชนิดใหม่ใน
    lawgraph.py แล้วลากจากคลาสผิด กราฟยังโหลดได้ ค้นได้ ไม่มีอะไรพัง
    แต่การเดินกราฟจะเดินไปผิดที่โดยไม่มีใครรู้ ไฟล์นี้ทำให้ความผิดแบบนั้นดังขึ้น

ทำไมไม่ใช้ rdflib กับ reasoner จริง
    เพราะต้องเพิ่ม dependency แล้วไป build image ใหม่ ซึ่งแลกไม่คุ้มกับสิ่งที่ได้
    ออนโทโลยีของเรามีห้าคลาสกับแปดความสัมพันธ์ เขียนตัวอ่านเองสั้นกว่าค่าติดตั้ง
    และเราคุมไวยากรณ์ของไฟล์เองทั้งไฟล์ จึงไม่ต้องรองรับ Turtle ทุกรูปแบบ
    ข้อเสียคือมันไม่ใช่ reasoner มาตรฐาน เราจึงเขียนกำกับไว้ตรง ๆ ทั้งที่นี่และในไฟล์ ttl
    ถ้าวันหนึ่งต้องส่งออนโทโลยีไปให้คนอื่นใช้ ไฟล์ ttl เป็น Turtle จริง เปิดด้วย
    rdflib หรือ Protege ได้ทันที ตัวอ่านนี้เลิกใช้ได้โดยไม่ต้องแก้ ttl

สิ่งที่ตรวจ
    1  ทุก node มีคลาสที่ประกาศไว้ และมีคลาสเดียว
    2  ทุกเส้นเป็นความสัมพันธ์ที่ประกาศไว้
    3  ปลายทั้งสองข้างของทุกเส้นตรงกับ domain และ range
    4  ปลายทั้งสองข้างมี node รองรับจริง ไม่มีเส้นชี้ไปในอากาศ
    5  FunctionalProperty มีปลายทางได้ไม่เกินหนึ่ง
    6  IrreflexiveProperty ไม่ชี้กลับมาที่ตัวเอง
    7  requiredForEveryRange ทุก node ในคลาสปลายทางต้องมีเส้นเข้าอย่างน้อยหนึ่ง
    8  เลขมาตราต้องเป็นจำนวนเต็มบวกและไม่เกินเพดาน maxPlausible
    9  เลขหัวข้อในตัวบทต้องไม่ปนเลขไทยกับเลขอารบิกในจำนวนเดียวกัน

ข้อ 9 มาจากไหน ทำไมต้องมีทั้งที่ดูจุกจิก
    ราชกิจจานุเบกษาพิมพ์เลขเชิงอรรถเป็นตัวยกติดกับเลขมาตรา พอดึงข้อความออกมา
    ตัวยกกลายเป็นตัวเลขธรรมดาต่อท้าย "มาตรา ๗๘" กับเชิงอรรถหมายเลข 93
    จึงกลายเป็น "มาตรา ๗๘93" แล้วตัวตัดก้อนอ่านเป็นมาตรา 7893
    ข้อ 8 จับกรณีนี้ได้เพราะเลขมันโตเกินจริง
    แต่กรณีที่อันตรายกว่าคือ "มาตรา ๒1" ซึ่งอ่านเป็นมาตรา 21
    เลข 21 เป็นมาตราที่มีจริง ข้อ 8 จึงปล่อยผ่าน และไม่มีใครเห็นว่าเนื้อในก้อน
    ที่ชื่อมาตรา 21 คือตัวบทของมาตรา ๒
    กฎหมายไทยไม่เคยเขียนเลขมาตราปนสองระบบในจำนวนเดียว ข้อ 9 จึงจับได้ทั้งหมด

สิ่งที่อนุมาน
    propertyChainAxiom ต่อโซ่สองเส้นให้เป็นเส้นใหม่หนึ่งเส้น
    ตอนนี้มีกฎเดียวคือ citesAct เขียนไว้ใน ontology.ttl ไม่ได้ฝังในโค้ดนี้
    เพิ่มกฎใหม่ทำที่ไฟล์ ttl ไฟล์เดียว ไม่ต้องแก้ไฟล์นี้

วิธีใช้
    python -m lawrag.ontology                    ตรวจแล้วรายงาน
    python -m lawrag.ontology --infer            ตรวจ แล้วเขียนเส้นที่อนุมานได้ลงไฟล์
    python -m lawrag.ontology --strict           เจอข้อผิดพลาดแล้วคืน exit code 1

ไฟล์เข้า : data/law/ontology.ttl  และ data/law/law_graph.json
ไฟล์ออก : data/law/law_graph_inferred.json  เฉพาะเมื่อใส่ --infer
          data/law/ontology_report.csv       รายการที่ไม่ผ่านทุกบรรทัด
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from collections import defaultdict

# ส่วนที่ 1: ที่อยู่ไฟล์
BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
TTL_PATH = os.path.join(LAW_DIR, "ontology.ttl")
GRAPH_PATH = os.path.join(LAW_DIR, "law_graph.json")
OUT_GRAPH = os.path.join(LAW_DIR, "law_graph_inferred.json")
OUT_REPORT = os.path.join(LAW_DIR, "ontology_report.csv")

THAI_DIGITS = "๐๑๒๓๔๕๖๗๘๙"
# จำนวนที่ปนเลขไทยกับเลขอารบิก ไม่ว่าฝั่งไหนขึ้นก่อน
RE_MIXED_HEAD = re.compile(r"(มาตรา|ข้อ)\s*([๐-๙]+[0-9]+|[0-9]+[๐-๙]+)")


# หน้าที่: แปลงเลขไทยเป็นเลขอารบิก · รับ: ข้อความ · คืน: ข้อความที่เลขเป็นอารบิก
def th2ar(s: str) -> str:
    return "".join(str(THAI_DIGITS.index(c)) if c in THAI_DIGITS else c for c in s)


# ส่วนที่ 2: ตัวอ่าน Turtle เฉพาะไวยากรณ์ที่เราใช้เอง
# หน้าที่: ตัดบรรทัดคอมเมนต์ · รับ: ข้อความทั้งไฟล์ · คืน: ข้อความที่ไม่มีคอมเมนต์
# ห้ามตัดด้วย regex ที่เจอ # แล้วตัดท้ายบรรทัด เพราะ IRI ของเราลงท้ายด้วย law#>
# จึงตัดเฉพาะบรรทัดที่ขึ้นต้นด้วย # ซึ่งเป็นรูปเดียวที่ไฟล์ ttl ของเราใช้
def strip_comments(text: str) -> str:
    keep = [ln for ln in text.splitlines() if not ln.lstrip().startswith("#")]
    return "\n".join(keep)


# หน้าที่: แบ่งข้อความเป็นประโยค · รับ: ข้อความ ttl · คืน: รายการประโยค
# ประโยค Turtle จบด้วยจุด แต่จุดอยู่ในเลขทศนิยมและใน IRI ได้ด้วย
# จึงนับจุดเป็นตัวจบประโยคเฉพาะเมื่ออยู่นอกเครื่องหมายคำพูดและนอกวงเล็บมุม
# และต้องมีช่องว่างหรือท้ายบรรทัดขนาบ ซึ่งเป็นรูปที่ไฟล์ของเราเขียนทั้งไฟล์
def split_statements(text: str) -> list[str]:
    out, buf = [], []
    in_str = in_iri = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"' and not in_iri:
            in_str = not in_str
        elif c == "<" and not in_str:
            in_iri = True
        elif c == ">" and not in_str:
            in_iri = False
        if c == "." and not in_str and not in_iri:
            prev = text[i - 1] if i else " "
            nxt = text[i + 1] if i + 1 < n else "\n"
            if prev in " \n\t" and nxt in " \n\t":
                out.append("".join(buf))
                buf = []
                i += 1
                continue
        buf.append(c)
        i += 1
    if "".join(buf).strip():
        out.append("".join(buf))
    return [s.strip() for s in out if s.strip()]


# หน้าที่: ดึงคู่ predicate กับ object จากหนึ่งประโยค
# รับ: ประโยค ttl หนึ่งประโยค · คืน: ชื่อ subject กับ dict ของ predicate -> รายการ object
# วงเล็บเหลี่ยมที่เป็น blank node เราข้าม ยกเว้น AllDisjointClasses ที่จัดการแยก
def parse_statement(stmt: str) -> tuple[str, dict[str, list[str]]]:
    stmt = stmt.strip()
    m = re.match(r"^(\[\s*\]|\S+)\s+(.*)$", stmt, re.S)
    if not m:
        return "", {}
    subj, rest = m.group(1), m.group(2)
    preds: dict[str, list[str]] = defaultdict(list)
    # แยกด้วยอัฒภาคที่อยู่นอกคำพูดและนอกวงเล็บ
    parts, buf, depth, in_str = [], [], 0, False
    for c in rest:
        if c == '"':
            in_str = not in_str
        elif not in_str and c in "([":
            depth += 1
        elif not in_str and c in ")]":
            depth -= 1
        if c == ";" and not in_str and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(c)
    parts.append("".join(buf))
    for p in parts:
        p = p.strip()
        if not p:
            continue
        m2 = re.match(r"^(\S+)\s+(.*)$", p, re.S)
        if not m2:
            continue
        pred, objs = m2.group(1), m2.group(2).strip()
        if objs.startswith("("):  # รายการเรียงลำดับ ใช้กับ propertyChainAxiom
            inner = objs[1 : objs.rindex(")")]
            preds[pred].append(" ".join(inner.split()))
            continue
        # แยกด้วยจุลภาคที่อยู่นอกคำพูด ใช้กับ a owl:ObjectProperty , owl:FunctionalProperty
        chunks, b, ins = [], [], False
        for c in objs:
            if c == '"':
                ins = not ins
            if c == "," and not ins:
                chunks.append("".join(b))
                b = []
                continue
            b.append(c)
        chunks.append("".join(b))
        for ch in chunks:
            ch = ch.strip()
            if ch.startswith('"'):
                ch = ch[1 : ch.index('"', 1)]
            preds[pred].append(ch)
    return subj, dict(preds)


class Ontology:
    """สิ่งที่อ่านได้จาก ontology.ttl ในรูปที่โค้ดเอาไปใช้ตรวจได้ทันที"""

    def __init__(self, path: str = TTL_PATH) -> None:
        text = strip_comments(open(path, encoding="utf-8").read())
        self.classes: dict[str, str] = {}       # ชื่อคลาส -> rdfs:label
        self.props: dict[str, dict] = {}        # label ของ relation -> คุณสมบัติ
        self.disjoint: list[str] = []
        self.max_section = None

        for stmt in split_statements(text):
            subj, preds = parse_statement(stmt)
            types = preds.get("a", [])
            if "owl:Class" in types:
                self.classes[subj] = (preds.get("rdfs:label") or [subj])[0]
            if "owl:AllDisjointClasses" in types:
                self.disjoint = (preds.get("owl:members") or [""])[0].split()
            if "owl:ObjectProperty" in types:
                label = (preds.get("rdfs:label") or [subj])[0]
                chain = preds.get("owl:propertyChainAxiom")
                self.props[label] = {
                    "iri": subj,
                    "domain": (preds.get("rdfs:domain") or [None])[0],
                    "range": (preds.get("rdfs:range") or [None])[0],
                    "functional": "owl:FunctionalProperty" in types,
                    "irreflexive": "owl:IrreflexiveProperty" in types,
                    "required_for_range": (preds.get("nbtc:requiredForEveryRange") or ["false"])[0] == "true",
                    "chain": chain[0].split() if chain else None,
                    "inferred": (preds.get("nbtc:inferred") or ["false"])[0] == "true",
                }
            if "owl:DatatypeProperty" in types and preds.get("nbtc:maxPlausible"):
                self.max_section = int(preds["nbtc:maxPlausible"][0])

        # แปลงชื่อคลาสจาก IRI ย่อเป็น label เพื่อเทียบกับฟิลด์ label ใน json ได้ตรง ๆ
        self.label_of = {k: v for k, v in self.classes.items()}

    # หน้าที่: คืนความสัมพันธ์ที่ต้องมีอยู่จริงในกราฟ ไม่รวมตัวที่อนุมาน
    def asserted(self) -> dict[str, dict]:
        return {k: v for k, v in self.props.items() if not v["inferred"]}

    # หน้าที่: คืนกฎอนุมานทั้งหมดที่ประกาศไว้
    def rules(self) -> dict[str, dict]:
        return {k: v for k, v in self.props.items() if v["chain"]}


# ส่วนที่ 3: การตรวจ
# หน้าที่: ตรวจกราฟกับออนโทโลยี · รับ: ออนโทโลยี กราฟ · คืน: รายการข้อผิดพลาด
def check(onto: Ontology, graph: dict) -> list[dict]:
    bad: list[dict] = []

    def err(kind, where, detail):
        bad.append({"ชนิดปัญหา": kind, "ที่เกิด": where, "รายละเอียด": detail})

    nodes = graph["nodes"]
    edges = graph["edges"]
    cls_of = {}
    ok_labels = set(onto.label_of.values())

    # ข้อ 1 คลาสของทุก node
    for nd in nodes:
        lab = nd.get("label")
        if lab not in ok_labels:
            err("คลาสไม่ได้ประกาศไว้", nd["id"], "label=%r ที่ประกาศไว้มี %s" % (lab, sorted(ok_labels)))
        cls_of[nd["id"]] = lab

    # ข้อ 8 เลขมาตรา
    if onto.max_section is not None:
        for nd in nodes:
            if nd.get("label") != "ActSection":
                continue
            num = nd.get("number")
            if not isinstance(num, int) or num < 1:
                err("เลขมาตราไม่เป็นจำนวนเต็มบวก", nd["id"], "number=%r" % (num,))
            elif num > onto.max_section:
                err("เลขมาตราเกินเพดาน", nd["id"],
                    "number=%d เพดาน %d เลขแบบนี้มักเกิดจากเลขเชิงอรรถพิมพ์ติดกับเลขมาตรา" % (num, onto.max_section))

    # ข้อ 9 เลขหัวข้อปนสองระบบ
    # ตรวจจาก preview ของ node คลาส Chunk เพราะ preview เก็บบรรทัดหัวข้อไว้ตามจริง
    for nd in nodes:
        if nd.get("label") != "Chunk":
            continue
        m = RE_MIXED_HEAD.search(nd.get("preview") or "")
        if m:
            unit, mixed = m.group(1), m.group(2)
            thai = "".join(c for c in mixed if c in THAI_DIGITS)
            arab = "".join(c for c in mixed if c.isdigit() and c not in THAI_DIGITS)
            err("เลขหัวข้อปนเลขไทยกับเลขอารบิก", nd["id"],
                "ตัวบทเขียน %s %s · เลขมาตราจริงคือ %s %s · ส่วน %s เป็นเลขเชิงอรรถ"
                % (unit, mixed, unit, th2ar(thai), arab))

    asserted = onto.asserted()
    seen_targets = defaultdict(set)
    func_seen = defaultdict(set)

    for e in edges:
        rel, s, t = e.get("relationship"), e.get("source"), e.get("target")
        # ข้อ 2 ความสัมพันธ์ต้องประกาศไว้
        if rel not in asserted:
            err("ความสัมพันธ์ไม่ได้ประกาศไว้", "%s -> %s" % (s, t), "relationship=%r" % (rel,))
            continue
        spec = asserted[rel]
        # ข้อ 4 ปลายต้องมี node รองรับ
        if s not in cls_of:
            err("ต้นทางไม่มี node รองรับ", "%s %s" % (rel, s), "")
            continue
        if t not in cls_of:
            err("ปลายทางไม่มี node รองรับ", "%s %s" % (rel, t), "")
            continue
        # ข้อ 3 domain กับ range
        want_d = onto.label_of.get(spec["domain"])
        want_r = onto.label_of.get(spec["range"])
        if want_d and cls_of[s] != want_d:
            err("ต้นทางผิด domain", "%s %s" % (rel, s),
                "ประกาศว่า %s แต่ node เป็น %s" % (want_d, cls_of[s]))
        if want_r and cls_of[t] != want_r:
            err("ปลายทางผิด range", "%s %s" % (rel, t),
                "ประกาศว่า %s แต่ node เป็น %s" % (want_r, cls_of[t]))
        # ข้อ 6 irreflexive
        if spec["irreflexive"] and s == t:
            err("ความสัมพันธ์ชี้กลับมาที่ตัวเอง", "%s %s" % (rel, s), "ประกาศเป็น IrreflexiveProperty")
        # ข้อ 5 functional
        if spec["functional"]:
            func_seen[(rel, s)].add(t)
        seen_targets[rel].add(t)

    for (rel, s), ts in func_seen.items():
        if len(ts) > 1:
            err("FunctionalProperty มีปลายทางเกินหนึ่ง", "%s %s" % (rel, s),
                "ชี้ไป %d ที่ : %s" % (len(ts), sorted(ts)[:3]))

    # ข้อ 7 ทุก node ในคลาสปลายทางต้องมีเส้นเข้า
    for rel, spec in asserted.items():
        if not spec["required_for_range"]:
            continue
        want_r = onto.label_of.get(spec["range"])
        for nd in nodes:
            if nd.get("label") == want_r and nd["id"] not in seen_targets[rel]:
                err("ขาดเส้นเข้าที่บังคับไว้", nd["id"],
                    "คลาส %s ต้องมีเส้น %s เข้าอย่างน้อยหนึ่งเส้น" % (want_r, rel))

    return bad


# ส่วนที่ 4: การอนุมาน
# หน้าที่: ต่อโซ่ตามกฎใน ttl · รับ: ออนโทโลยี กราฟ · คืน: รายการเส้นใหม่
def infer(onto: Ontology, graph: dict) -> list[dict]:
    by_rel: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    have = set()
    for e in graph["edges"]:
        by_rel[e["relationship"]][e["source"]].add(e["target"])
        have.add((e["source"], e["relationship"], e["target"]))

    iri2label = {v["iri"]: k for k, v in onto.props.items()}
    new: list[dict] = []
    for label, spec in onto.rules().items():
        chain = [iri2label.get(x, x) for x in spec["chain"]]
        if any(c not in by_rel for c in chain):
            continue
        # เดินโซ่จากซ้ายไปขวา เริ่มจากต้นทางทุกตัวของเส้นแรก
        frontier = {s: {s} for s in by_rel[chain[0]]}
        reach = {s: {s} for s in by_rel[chain[0]]}
        for step in chain:
            nxt = {}
            for s, cur in reach.items():
                acc = set()
                for mid in cur:
                    acc |= by_rel[step].get(mid, set())
                nxt[s] = acc
            reach = nxt
        for s, targets in reach.items():
            for t in targets:
                if (s, label, t) in have:
                    continue
                new.append({"source": s, "target": t, "relationship": label, "inferred": True})
        del frontier
    return new


def main() -> int:
    ap = argparse.ArgumentParser(description="ตรวจกราฟกฎหมายกับออนโทโลยี และอนุมานเส้นใหม่")
    ap.add_argument("--infer", action="store_true", help="เขียนกราฟที่รวมเส้นอนุมานแล้วลงไฟล์")
    ap.add_argument("--strict", action="store_true", help="ถ้าไม่ผ่านให้คืน exit code 1")
    ap.add_argument("--show", type=int, default=12, help="จำนวนบรรทัดที่พิมพ์ให้ดูต่อหนึ่งชนิดปัญหา")
    args = ap.parse_args()

    onto = Ontology()
    graph = json.load(open(GRAPH_PATH, encoding="utf-8"))

    print("ออนโทโลยี %d คลาส · %d ความสัมพันธ์ที่ต้องมีจริง · %d กฎอนุมาน · เพดานเลขมาตรา %s"
          % (len(onto.classes), len(onto.asserted()), len(onto.rules()), onto.max_section))
    print("กราฟ %d node · %d เส้น" % (len(graph["nodes"]), len(graph["edges"])))
    print()

    bad = check(onto, graph)
    if not bad:
        print("ผ่านทุกข้อ กราฟตรงกับที่ประกาศไว้ทั้งหมด")
    else:
        kinds: dict[str, list] = defaultdict(list)
        for b in bad:
            kinds[b["ชนิดปัญหา"]].append(b)
        print("ไม่ผ่าน %d จุด แยกเป็น %d ชนิด" % (len(bad), len(kinds)))
        for k, items in sorted(kinds.items(), key=lambda kv: -len(kv[1])):
            print("\n  %s : %d จุด" % (k, len(items)))
            for it in items[: args.show]:
                print("     %s  %s" % (it["ที่เกิด"], it["รายละเอียด"]))
            if len(items) > args.show:
                print("     ... อีก %d จุด ดูครบใน %s" % (len(items) - args.show, os.path.basename(OUT_REPORT)))
        with open(OUT_REPORT, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["ชนิดปัญหา", "ที่เกิด", "รายละเอียด"])
            w.writeheader()
            w.writerows(bad)
        print("\nเขียนรายการครบทุกบรรทัดไว้ที่ %s" % OUT_REPORT)

    new = infer(onto, graph)
    print()
    for label, spec in onto.rules().items():
        iri2label = {v["iri"]: k for k, v in onto.props.items()}
        chain = " ต่อ ".join(iri2label.get(x, x) for x in spec["chain"])
        got = [e for e in new if e["relationship"] == label]
        src = {e["source"] for e in got}
        print("กฎ %s = %s" % (label, chain))
        print("   อนุมานได้ %d เส้นใหม่ · ครอบคลุมต้นทาง %d ตัว" % (len(got), len(src)))

    if args.infer:
        out = {"nodes": graph["nodes"], "edges": graph["edges"] + new}
        json.dump(out, open(OUT_GRAPH, "w", encoding="utf-8"), ensure_ascii=False)
        print("\nเขียนกราฟที่รวมเส้นอนุมานแล้ว %d เส้น ไว้ที่ %s"
              % (len(out["edges"]), OUT_GRAPH))

    return 1 if (bad and args.strict) else 0


if __name__ == "__main__":
    sys.exit(main())
