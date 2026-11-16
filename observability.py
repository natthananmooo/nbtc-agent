"""
observability.py - บันทึก context ของทุกคำถามลง Langfuse

เก็บไว้ทำอะไร
    correctness   คำตอบตรงกับความจริงไหม เทียบกับเฉลยที่คำนวณจาก SQL โดยตรง
    faithfulness  ตัวเลขในคำตอบมาจากข้อมูลที่ดึงมาจริงไหม หรือโมเดลเติมเอง

เพราะอย่างหลังต้องเทียบกับ "ข้อมูลที่ดึงมา" จึงต้องบันทึกให้ครบ
    - คำที่ผู้ใช้พิมพ์ และค่าที่ resolve ได้ พร้อม confidence
    - SQL ที่รันจริง พารามิเตอร์ และแถวที่ได้
    - warnings ทุกข้อ
    - คำตอบสุดท้ายของโมเดล

ถ้ายังไม่ได้ใส่คีย์ใน .env ตัวนี้จะเงียบและไม่ทำอะไร ระบบยังรันได้ปกติ
"""

from __future__ import annotations

import os
from contextlib import contextmanager

# tool สองตัวนี้คือขั้นตอน "ดึงข้อมูล" บันทึกเป็น retriever
# เพื่อให้เครื่องมือประเมิน faithfulness รู้ว่าอะไรคือ context ที่ใช้อ้างอิง
RETRIEVERS = {"resolve_filter_values", "query_complaints"}


class Tracer:
    """ห่อ Langfuse ไว้ ถ้าไม่มีคีย์ก็กลายเป็นตัวเปล่าที่ไม่ทำอะไร"""

    def __init__(self):
        self.client = None
        self.context: list[dict] = []     # ข้อมูลที่ดึงมาในรอบนี้ ใช้วัด faithfulness
        self.last_trace_id: str | None = None
        self._tried = False

    @property
    def run_label(self):
        """อ่านตอนใช้ ไม่ใช่ตอนสร้าง เพราะ eval ตั้งค่านี้หลัง import"""
        return os.environ.get("RUN_LABEL")

    def _connect(self):
        """ต่อ Langfuse ตอนถูกเรียกใช้ครั้งแรก ไม่ใช่ตอน import

        เดิมต่อใน __init__ ซึ่งทำงานตอน import observability
        ถ้าโมดูลนั้นถูก import ก่อน load_dotenv คีย์จะยังไม่อยู่ใน env
        tracer จึงถูกปิดเงียบ ๆ แล้วไม่มีใครรู้จนเปิดหน้าเว็บ Langfuse แล้วว่าง
        """
        if self._tried:
            return
        self._tried = True
        pub = os.environ.get("LANGFUSE_PUBLIC_KEY")
        sec = os.environ.get("LANGFUSE_SECRET_KEY")
        if not (pub and sec):
            return
        try:
            from langfuse import Langfuse
            self.client = Langfuse(
                public_key=pub,
                secret_key=sec,
                base_url=os.environ.get("LANGFUSE_BASE_URL")
                         or os.environ.get("LANGFUSE_HOST")
                         or "https://cloud.langfuse.com",
                environment=os.environ.get("LANGFUSE_ENV", "development"),
            )
        except Exception as e:
            print("[เปิด Langfuse ไม่ได้ ข้ามการบันทึก: %s]" % e)

    @property
    def enabled(self) -> bool:
        self._connect()
        return self.client is not None

    # ------------------------------------------------------------ หนึ่งคำถาม
    @contextmanager
    def question(self, text: str, session_id: str | None = None,
                 system_prompt: str | None = None):
        self.context = []
        if not self.enabled:
            yield None
            return
        meta = {}
        if session_id:
            meta["session_id"] = session_id
        if self.run_label:
            meta["run_label"] = self.run_label
        if system_prompt:
            # วางไว้ที่หัว trace ด้วย เพื่อให้หาคำสั่งที่โมเดลได้รับเจอในที่เดียว
            # ไม่ต้องไล่เปิด generation ทีละขั้น
            meta["system_prompt"] = system_prompt
        with self.client.start_as_current_observation(
            name="ถาม-ตอบหนึ่งรอบ", as_type="agent", input={"question": text},
            metadata=meta or None,
        ) as root:
            self.last_trace_id = self.client.get_current_trace_id()
            try:
                yield root
            finally:
                self.client.flush()

    # ------------------------------------------------------- เรียก tool หนึ่งครั้ง
    @contextmanager
    def tool_call(self, name: str, args: dict):
        kind = "retriever" if name in RETRIEVERS else "tool"
        if not self.enabled:
            yield _Null(self, name, kind)
            return
        with self.client.start_as_current_observation(
            name=name, as_type=kind, input=args,
        ) as span:
            yield _Span(self, name, kind, span)

    # ------------------------------------------------------------ เรียกโมเดล
    @contextmanager
    def generation(self, model: str, messages: list):
        if not self.enabled:
            yield _Null(self, model, "generation")
            return
        with self.client.start_as_current_observation(
            name="llm", as_type="generation", model=model, input=messages,
        ) as gen:
            yield _Span(self, model, "generation", gen)

    # ------------------------------------------------------- ปิดท้ายทั้งรอบ
    def finish(self, question: str, answer: str):
        """ผูกคำตอบสุดท้ายเข้ากับ context ที่ดึงมา เพื่อให้ประเมินผลได้"""
        if not self.enabled:
            return
        self.client.update_current_span(
            output={"answer": answer},
            metadata={
                "retrieved_context": self.context,
                "context_count": len(self.context),
            },
        )
        # เมธอดนี้มีไม่ครบทุกเวอร์ชันของ langfuse ถ้าไม่มีก็ข้าม
        # trace ยังถูกบันทึกครบ เสียแค่ข้อความสรุปที่หัว trace
        setter = getattr(self.client, "set_current_trace_io", None)
        if setter:
            setter(input=question, output=answer)
        else:
            self.client.update_current_span(
                input={"question": question}, output={"answer": answer})


    # ------------------------------------------------------------ ให้คะแนน
    def add_score(self, name: str, value: float, comment: str = "",
                  trace_id: str | None = None):
        """ส่งคะแนน correctness หรือ faithfulness ผูกกับ trace ของคำถามนั้น"""
        tid = trace_id or self.last_trace_id
        if not (self.enabled and tid):
            return
        self.client.create_score(
            name=name, value=float(value), trace_id=tid,
            data_type="NUMERIC", comment=comment[:1800] or None)
        self.client.flush()


class _Span:
    def __init__(self, tracer, name, kind, span):
        self.tracer, self.name, self.kind, self.span = tracer, name, kind, span

    def done(self, output, **meta):
        # เก็บผลของ tool ทุกตัว (ยกเว้นคำตอบของโมเดล) ไว้เป็น context
        # เพราะตัวเลขที่ analyze_results คำนวณ ก็ถือว่ามาจากข้อมูลจริงเช่นกัน
        if self.kind != "generation":
            self.tracer.context.append(
                {"tool": self.name, "kind": self.kind, "output": output})
        if self.span is not None:
            self.span.update(output=output, metadata=meta or None)


class _Null(_Span):
    def __init__(self, tracer, name, kind):
        super().__init__(tracer, name, kind, None)


tracer = Tracer()
