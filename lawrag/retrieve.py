"""
lawrag/retrieve.py - ค้นคลังกฎหมาย 7 วิธี เพื่อเทียบกันตามแนวของ Anthropic

หกวิธีแรกคือของ Anthropic ในบทความ Contextual Retrieval
    กลุ่ม Standard   ใช้ดัชนีที่สร้างจาก text            คือหัวเรื่องโครงสร้าง + ตัวบท
    กลุ่ม Contextual ใช้ดัชนีที่สร้างจาก text_contextual คือคำบรรยายที่ LLM เขียน + ข้อความเดียวกัน
    แต่ละกลุ่มมีสามชั้น  เวกเตอร์อย่างเดียว · เพิ่ม BM25 · เพิ่ม cross-encoder จัดอันดับ

วิธีที่เจ็ดเป็นของเราเอง
    เพิ่มการเดินกราฟการอ้างอิงต่อจากวิธีที่หก
    Anthropic ไม่ได้วัดข้อนี้เพราะบทความนั้นไม่มีกราฟ
    ที่ต้องมีเพราะคำถามเรื่องกฎหมายมักมีสองท่อน ข้อห้ามอยู่ฉบับหนึ่ง บทลงโทษอยู่อีกฉบับ
    และสองที่นั้นไม่มีคำร่วมกันเลย เวกเตอร์กับ BM25 จึงหาท่อนหลังไม่เจอไม่ว่าจะปรับยังไง

เรื่องการรวมสองลำดับเข้าด้วยกัน
    ใช้ Reciprocal Rank Fusion ตามที่ Qdrant กับบทความของอาจารย์ใช้
    ข้อดีคือไม่ต้องตั้งน้ำหนักระหว่าง dense กับ BM25 ซึ่งเป็นค่าที่จูนยากและย้ายโดเมนแล้วเพี้ยน
    RRF ดูแค่อันดับ ไม่ดูคะแนนดิบ เอกสารที่ติดอันดับดีในทั้งสองลำดับจึงลอยขึ้นมาเอง
"""
from __future__ import annotations

import json
import os
import pickle
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
LAW_DIR = os.environ.get("LAW_DIR", os.path.join(BASE, "..", "data", "law"))
PGDSN = os.environ.get("PGDSN", "postgresql://law:law@localhost:5433/law")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-m3")
RERANK_MODEL = os.environ.get("RERANK_MODEL", "BAAI/bge-reranker-v2-m3")

RRF_K = 60        # ค่ามาตรฐานของ RRF ยิ่งมากยิ่งให้อันดับท้าย ๆ มีน้ำหนักใกล้อันดับต้น
CAND = 30         # จำนวนผู้เข้าชิงที่ส่งให้ cross-encoder จัดอันดับใหม่
GRAPH_HOPS = 2
GRAPH_CAP = 25

# ชื่อวิธี -> (ชุดดัชนีที่ใช้, ใช้ BM25 ไหม, จัดอันดับใหม่ไหม, เดินกราฟไหม)
METHODS: dict[str, tuple[str, bool, bool, bool]] = {
    "embedding":                 ("plain", False, False, False),
    "embedding+bm25":            ("plain", True,  False, False),
    "rerank+embedding+bm25":     ("plain", True,  True,  False),
    "ctx_embedding":             ("ctx",   False, False, False),
    "ctx_embedding+ctx_bm25":    ("ctx",   True,  False, False),
    "rerank+ctx_embedding+bm25": ("ctx",   True,  True,  False),
    "graph+rerank+ctx":          ("ctx",   True,  True,  True),
}


# ส่วนที่ 1: รวมหลายลำดับด้วย Reciprocal Rank Fusion
# หน้าที่: รวมลำดับที่ได้จากหลายวิธีเป็นลำดับเดียว โดยดูอันดับ ไม่ดูคะแนนดิบ
# รับ: รายการของลำดับ แต่ละลำดับเป็นลิสต์ของ id เรียงจากดีไปแย่
# คืน: ลิสต์ (id, คะแนนรวม) เรียงจากดีไปแย่
def rrf(rank_lists: list[list[str]]) -> list[tuple[str, float]]:
    score: dict[str, float] = defaultdict(float)
    for lst in rank_lists:
        for rank, cid in enumerate(lst, 1):
            score[cid] += 1.0 / (RRF_K + rank)
    return sorted(score.items(), key=lambda x: -x[1])


# ส่วนที่ 2: ตัวค้นหลัก
class LawRetriever:
    """ถือโมเดล ฐานข้อมูล ดัชนี BM25 และกราฟไว้ที่เดียว เพื่อไม่ต้องโหลดซ้ำทุกคำถาม"""

    # หน้าที่: โหลดทุกอย่างที่ต้องใช้ · รับ: ต้องการ reranker และกราฟไหม
    def __init__(self, with_rerank: bool = True, with_graph: bool = True) -> None:
        import psycopg
        from sentence_transformers import SentenceTransformer

        self.conn = psycopg.connect(PGDSN)
        self.embed = SentenceTransformer(EMBED_MODEL)
        self.bm25: dict[str, dict] = {}
        for v in ("plain", "ctx"):
            with open(os.path.join(LAW_DIR, f"bm25_{v}.pkl"), "rb") as f:
                self.bm25[v] = pickle.load(f)

        # เก็บข้อความไว้ในหน่วยความจำ เพราะ cross-encoder ต้องใช้ทุกครั้งที่จัดอันดับ
        with self.conn.cursor() as cur:
            cur.execute("SELECT id, doc_title, unit, number, body, text, text_ctx FROM law_chunk")
            self.meta = {r[0]: {"doc_title": r[1], "unit": r[2], "number": r[3],
                                "body": r[4], "plain": r[5], "ctx": r[6]}
                         for r in cur.fetchall()}

        self.reranker = None
        if with_rerank:
            from sentence_transformers import CrossEncoder
            self.reranker = CrossEncoder(RERANK_MODEL, max_length=512)

        self.graph = None
        if with_graph:
            g = json.load(open(os.path.join(LAW_DIR, "law_graph.json"), encoding="utf-8"))
            self.gnodes = {n["id"]: n for n in g["nodes"]}
            self.gedges = g["edges"]
            self.graph = True

    # หน้าที่: ค้นด้วยเวกเตอร์ · รับ: คำถาม ชุดดัชนี จำนวน · คืน: ลิสต์ id เรียงจากใกล้สุด
    def dense(self, query: str, variant: str, k: int) -> list[str]:
        v = self.embed.encode([query], normalize_embeddings=True)[0]
        lit = "[" + ",".join("%.6f" % x for x in v) + "]"
        with self.conn.cursor() as cur:
            # <=> คือระยะ cosine ของ pgvector ยิ่งน้อยยิ่งใกล้
            cur.execute(f"""SELECT chunk_id FROM law_vec_{variant}
                            ORDER BY emb <=> %s::vector LIMIT %s""", (lit, k))
            return [r[0] for r in cur.fetchall()]

    # หน้าที่: ค้นด้วย BM25 · รับ: คำถาม ชุดดัชนี จำนวน · คืน: ลิสต์ id เรียงจากคะแนนสูงสุด
    def lexical(self, query: str, variant: str, k: int) -> list[str]:
        from .index import tokenize
        d = self.bm25[variant]
        scores = d["bm25"].get_scores(tokenize(query))
        order = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        return [d["ids"][i] for i in order]

    # หน้าที่: จัดอันดับใหม่ด้วย cross-encoder ซึ่งอ่านคำถามกับเอกสารพร้อมกัน
    # รับ: คำถาม รายชื่อผู้เข้าชิง ชุดข้อความที่ใช้ จำนวนที่ต้องการ · คืน: ลิสต์ id เรียงใหม่
    def rerank(self, query: str, ids: list[str], variant: str, k: int) -> list[str]:
        if not self.reranker or not ids:
            return ids[:k]
        pairs = [(query, self.meta[i][variant]) for i in ids if i in self.meta]
        valid = [i for i in ids if i in self.meta]
        scores = self.reranker.predict(pairs)
        return [i for _, i in sorted(zip(scores, valid), key=lambda x: -x[0])][:k]

    # หน้าที่: ขยายผลด้วยการเดินกราฟการอ้างอิง
    # รับ: id ตั้งต้น · คืน: ลิสต์ id ของ chunk ที่เดินไปถึง โดยไม่ซ้ำกับตั้งต้น
    def expand(self, seed_ids: list[str]) -> list[str]:
        if not self.graph:
            return []
        from .lawgraph import traverse
        seeds = ["chunk::" + i for i in seed_ids if "chunk::" + i in self.gnodes]
        if not seeds:
            return []
        found = traverse(self.gnodes, self.gedges, seeds,
                         hops=GRAPH_HOPS, cap=GRAPH_CAP)
        out = []
        for _, _, nid in found:
            n = self.gnodes.get(nid, {})
            if n.get("label") == "Chunk":
                cid = nid.replace("chunk::", "", 1)
                if cid not in seed_ids and cid in self.meta:
                    out.append(cid)
        return out

    # หน้าที่: ค้นด้วยวิธีที่ระบุ · รับ: คำถาม ชื่อวิธี จำนวนที่ต้องการ · คืน: ลิสต์ id
    def search(self, query: str, method: str, k: int = 20) -> list[str]:
        variant, use_bm25, use_rerank, use_graph = METHODS[method]
        pool = CAND if (use_rerank or use_graph) else k

        lists = [self.dense(query, variant, pool)]
        if use_bm25:
            lists.append(self.lexical(query, variant, pool))
        fused = [cid for cid, _ in rrf(lists)]

        if use_rerank:
            fused = self.rerank(query, fused[:CAND], variant, k)
        if use_graph:
            # เอาผลที่จัดอันดับแล้วไว้ก่อน ตามด้วยสิ่งที่เดินกราฟไปเจอ
            # ไม่เอาไปรวมแล้วจัดอันดับใหม่ เพราะของที่เดินกราฟมาได้มาด้วยเหตุผลคนละแบบ
            # มันเกี่ยวข้องเพราะถูกอ้างถึง ไม่ใช่เพราะข้อความคล้ายคำถาม
            extra = self.expand(fused[:5])
            fused = fused + [e for e in extra if e not in fused]
        return fused[:k]


# ส่วนที่ 3: ทดลองค้นด้วยคำถามจริง ไว้ดูว่าแต่ละวิธีคืนอะไรต่างกัน
def selftest() -> int:
    questions = [
        "ร้องเรียนแล้วต้องได้คำตอบภายในกี่วัน",
        "คิดค่าบริการเกินจริง ผิดกฎหมายอะไร",
        "ผู้ให้บริการระงับสัญญาณโดยไม่แจ้งล่วงหน้าได้ไหม",
    ]
    r = LawRetriever(with_rerank=True, with_graph=True)
    for q in questions:
        print("=" * 78)
        print("คำถาม:", q)
        for method in METHODS:
            ids = r.search(q, method, k=3)
            print("\n  %-26s" % method)
            for i, cid in enumerate(ids, 1):
                m = r.meta[cid]
                label = "%s %s" % (m["unit"], m["number"])
                print("    %d. %-18s %-40s %s"
                      % (i, label, m["doc_title"][:38], m["body"][:46].replace("\n", " ")))
    return 0


if __name__ == "__main__":
    sys.exit(selftest())
