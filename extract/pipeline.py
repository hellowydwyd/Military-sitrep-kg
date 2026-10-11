"""OpenIE 流水线：切块 → LLM → 校验。"""

from __future__ import annotations

from extract.chunk import chunk_doc
from extract.llm import chat_json
from extract.prompt import build_user_prompt, parse_llm_json, system_for
from extract.validate import validate_extraction
from ingest.envelope import NewsDoc


def extract_doc(doc: NewsDoc) -> dict:
    chunks = chunk_doc(doc)
    out_chunks = []
    lang = doc.lang or ""
    system = system_for(lang)
    for ch in chunks:
        raw_text = chat_json(system, build_user_prompt(ch.prompt_text, lang=lang))
        parsed = parse_llm_json(raw_text)
        checked = validate_extraction(parsed, title=doc.title, body=ch.text)
        out_chunks.append(
            {
                "chunk_id": ch.chunk_id,
                "ord": ch.ord,
                "text": ch.text,
                "triples": checked["triples"],
                "events": checked["events"],
                "dropped_triples": checked["dropped_triples"],
                "dropped_events": checked["dropped_events"],
            }
        )
    extra = doc.extra or {}
    return {
        "doc_id": doc.doc_id,
        "title": doc.title,
        "publish_time": doc.publish_time,
        "url": doc.url,
        "source": doc.source,
        "source_name": extra.get("outlet") or doc.source_name,
        "lang": lang,
        "n_chunks": len(chunks),
        "chunks": out_chunks,
    }
