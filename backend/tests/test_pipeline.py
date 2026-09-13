"""测试解析/清洗/入库（离线）。"""
from __future__ import annotations

import pytest

from app.pipeline.cleaner import TextCleaner
from app.pipeline.chunker import Chunker
from app.pipeline.parser import Block, DocumentParser, ParsedDocument


def test_parse_text(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("第一章 总则\n第一条 这是制度内容。\n第二条 这是另一条。", encoding="utf-8")
    doc = DocumentParser().parse(p)
    assert doc.blocks
    assert any(b.type == "heading" for b in doc.blocks)


def test_parse_markdown(tmp_path):
    p = tmp_path / "a.md"
    p.write_text("# 选课办法\n\n- 第一项\n- 第二项\n", encoding="utf-8")
    doc = DocumentParser().parse(p)
    assert doc.blocks[0].type == "heading"
    assert doc.blocks[0].text == "选课办法"


def test_cleaner_fullwidth():
    cleaner = TextCleaner()
    assert cleaner.clean_text("全角：１２３") == "全角:123"


def test_cleaner_removes_noise():
    cleaner = TextCleaner()
    doc = ParsedDocument(title="t", blocks=[Block(type="paragraph", level=0, text="第 1 页 共 3 页")])
    out = cleaner.clean(doc)
    assert out.blocks == []
