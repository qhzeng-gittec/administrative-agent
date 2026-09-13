"""测试语义切片。"""
from __future__ import annotations

from app.pipeline.chunker import Chunker
from app.pipeline.parser import Block, ParsedDocument


def _doc(blocks):
    return ParsedDocument(title="测试办法", blocks=blocks)


def test_basic_chunking():
    blocks = [
        Block(type="heading", level=1, text="第一章 总则"),
        Block(type="paragraph", level=0, text="第一条 " + "制度内容" * 80),
        Block(type="paragraph", level=0, text="第二条 " + "条款内容" * 80),
    ]
    chunks = Chunker(min_chars=100, max_chars=400).chunk(_doc(blocks))
    assert chunks, "应产生至少一个 chunk"
    for c in chunks:
        assert c["content"]
        assert c["content_hash"]
        assert c["char_count"] == len(c["content"])
        assert c["section_path"][0] == "第一章 总则"


def test_chunk_respects_size_bounds():
    blocks = [Block(type="paragraph", level=0, text="第X条 " + "很长的条款内容" * 300)]
    chunks = Chunker(min_chars=200, max_chars=600).chunk(_doc(blocks))
    assert len(chunks) >= 2
    assert all(c["char_count"] <= 650 for c in chunks)


def test_empty_document():
    chunks = Chunker().chunk(_doc([]))
    assert chunks == []
