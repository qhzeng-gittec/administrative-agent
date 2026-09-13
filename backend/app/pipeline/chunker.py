"""语义切片：按"条款"语义边界切分（章/节/条/款），目标 300-600 字。

不按固定 token 硬切，保证每条回答可溯源到具体条款。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Optional

from app.pipeline.parser import Block, ParsedDocument

# 目标 chunk 大小（字符）
MIN_CHARS = 300
MAX_CHARS = 600

# 条款边界：第X条 / 第X章 / 数字编号 / 一、二、...
CLAUSE_RE = re.compile(r"^(第[一二三四五六七八九十百0-9]+[章节条款]|[一二三四五六七八九十]+、|\d+[.、．]|[（(]\d+[)）])")


class Chunker:
    def __init__(self, min_chars: int = MIN_CHARS, max_chars: int = MAX_CHARS) -> None:
        self.min_chars = min_chars
        self.max_chars = max_chars

    def chunk(self, doc: ParsedDocument) -> list[dict[str, Any]]:
        """返回切片草稿列表（未含 doc_id/embedding）。"""
        sections = self._build_sections(doc.blocks)
        chunks: list[dict[str, Any]] = []
        for section in sections:
            chunks.extend(self._chunk_section(section))
        # 全局重排 index
        for i, c in enumerate(chunks):
            c["chunk_index"] = i
            c["content_hash"] = hashlib.sha256(c["content"].encode("utf-8")).hexdigest()
            c["char_count"] = len(c["content"])
        return chunks

    def _build_sections(self, blocks: list[Block]) -> list[dict[str, Any]]:
        """把块按标题层级组装成 section（带 section_path）。"""
        sections: list[dict[str, Any]] = []
        current: Optional[dict[str, Any]] = None
        # heading 栈：维护各层级标题
        stack: list[str] = []
        for b in blocks:
            if b.type == "heading":
                # 更新标题栈
                while stack and b.level <= len(stack):
                    stack.pop()
                # 以当前块作为新 section 起点（标题本身进入 path）
                while len(stack) < b.level - 1:
                    stack.append("")
                stack.append(b.text)
                current = {"path": list(stack), "title": b.text, "blocks": [], "page": b.page}
                sections.append(current)
            else:
                if current is None:
                    current = {"path": [], "title": "", "blocks": [], "page": b.page}
                    sections.append(current)
                current["blocks"].append(b)
                if b.page is not None and current["page"] is None:
                    current["page"] = b.page
        return sections

    def _chunk_section(self, section: dict[str, Any]) -> list[dict[str, Any]]:
        units = self._split_units(section["blocks"])
        chunks: list[dict[str, Any]] = []
        buf: list[str] = []
        buf_len = 0
        pages: list[int] = []
        has_table = False

        def flush() -> None:
            nonlocal buf, buf_len, has_table
            if not buf:
                return
            content = "\n".join(buf).strip()
            if content:
                chunks.append(
                    {
                        "section_path": section["path"],
                        "section_title": section["title"],
                        "content": content,
                        "char_count": len(content),
                        "metadata": {"page": pages[0] if pages else None, "has_table": has_table},
                    }
                )
            buf = []
            buf_len = 0
            has_table = False

        for unit in units:
            text, page, is_table = unit
            for piece in self._hard_split(text):
                if buf_len + len(piece) > self.max_chars and buf_len >= self.min_chars:
                    flush()
                buf.append(piece)
                buf_len += len(piece)
                if page is not None and not pages:
                    pages.append(page)
                has_table = has_table or is_table
        flush()

        # 过短 chunk 与前一个合并（若都短）
        return self._merge_short(chunks)

    def _split_units(self, blocks: list[Block]) -> list[tuple[str, Optional[int], bool]]:
        """把段落块拆成最小语义单元（按条款边界）。"""
        units: list[tuple[str, Optional[int], bool]] = []
        for b in blocks:
            is_table = b.type == "table"
            if is_table:
                units.append((b.text, b.page, True))
                continue
            lines = [ln.strip() for ln in b.text.splitlines() if ln.strip()]
            for ln in lines:
                # 一条段落内可能包含多个条款，按编号拆分
                parts = self._split_clause_line(ln)
                for p in parts:
                    units.append((p, b.page, False))
        return units

    @staticmethod
    def _split_clause_line(line: str) -> list[str]:
        """将一行中多个编号条款拆开（保守：仅当编号在句中出现时拆分）。"""
        positions = [m.start() for m in CLAUSE_RE.finditer(line)]
        if len(positions) <= 1:
            return [line]
        parts = []
        for i, pos in enumerate(positions):
            end = positions[i + 1] if i + 1 < len(positions) else len(line)
            part = line[pos:end].strip()
            if part:
                parts.append(part)
        # 若拆分后过碎（平均 < 10 字）则保持原行
        if parts and sum(len(p) for p in parts) / len(parts) < 10:
            return [line]
        return parts or [line]

    def _hard_split(self, text: str) -> list[str]:
        """将超出 max_chars 的单元按句子边界/等长硬切。"""
        if len(text) <= self.max_chars:
            return [text]
        pieces: list[str] = []
        buf = ""
        # 优先按句末标点切
        for ch in text:
            buf += ch
            if ch in "。！？；\n" and len(buf) >= self.min_chars:
                pieces.append(buf)
                buf = ""
        if buf:
            pieces.append(buf)
        # 仍过长的等长硬切
        final: list[str] = []
        for p in pieces:
            while len(p) > self.max_chars:
                final.append(p[: self.max_chars])
                p = p[self.max_chars :]
            if p:
                final.append(p)
        return final or [text]

    def _merge_short(self, chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        for c in chunks:
            if merged and (len(c["content"]) < self.min_chars or len(merged[-1]["content"]) < self.min_chars):
                last = merged[-1]
                last["content"] = (last["content"] + "\n" + c["content"]).strip()
                last["char_count"] = len(last["content"])
                last["metadata"]["has_table"] = last["metadata"].get("has_table") or c["metadata"].get("has_table")
            else:
                merged.append(c)
        return merged
