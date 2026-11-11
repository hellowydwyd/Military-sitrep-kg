"""按段切块；短讯整篇一块。标题和发稿时间写入块前缀。"""

from __future__ import annotations

from dataclasses import asdict, dataclass

MAX_CHARS = 900


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    ord: int
    title: str
    publish_time: str
    text: str
    prompt_text: str

    def to_dict(self) -> dict:
        return asdict(self)


def chunk_doc(doc, *, max_chars: int = MAX_CHARS) -> list[Chunk]:
    paras = [p.strip() for p in (doc.body or "").split("\n") if p.strip()]
    if not paras:
        paras = [(doc.body or "").strip()]
    groups: list[str] = []
    buf: list[str] = []
    n = 0
    for p in paras:
        if buf and n + len(p) + 1 > max_chars:
            groups.append("\n".join(buf))
            buf, n = [p], len(p)
        else:
            buf.append(p)
            n += len(p) + 1
    if buf:
        groups.append("\n".join(buf))
    chunks = []
    for i, text in enumerate(groups):
        prefix = f"【标题】{doc.title}\n【发稿时间】{doc.publish_time}\n【正文】\n{text}"
        chunks.append(
            Chunk(
                chunk_id=f"{doc.doc_id[:12]}_{i:02d}",
                doc_id=doc.doc_id,
                ord=i,
                title=doc.title,
                publish_time=doc.publish_time,
                text=text,
                prompt_text=prefix,
            )
        )
    return chunks
