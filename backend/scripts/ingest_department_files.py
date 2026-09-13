"""导入 department_files 下的示例部门文档。

目录名 → 部门 id 映射；递归查找 pdf/docx/md/txt/html。
用法：python -m scripts.ingest_department_files [--base ../department_files]
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.config import get_settings
from app.deps import build_container

# 目录名（或关键词）→ dept_id 映射
DEPT_MAP = {
    "教务处": "dept_jwc",
    "学生处": "dept_xsc",
    "财务处": "dept_cwc",
    "人事处": "dept_rsc",
    "后勤": "dept_hqaq",      # 后勤与安全保卫部（已合并，无独立后勤处）
    "研究生院": "dept_yjsy",
    "中法学院": "dept_zfxy",
    "安全保卫": "dept_hqaq",
}

SUFFIXES = {".pdf", ".docx", ".doc", ".md", ".markdown", ".txt", ".html", ".htm"}

# 候选目录（按优先级）：本地 backend/ 目录、Docker 容器内 /app、compose 挂载点
BASE_CANDIDATES = ["../department_files", "/app/department_files", "department_files"]


def resolve_base(explicit: str | None) -> Path:
    """解析示例文档根目录：优先显式指定，否则取第一个存在的候选路径。"""
    if explicit:
        p = Path(explicit)
        if p.exists():
            return p.resolve()
        print(f"警告: 指定目录不存在，尝试候选路径: {explicit}")
    for cand in BASE_CANDIDATES:
        p = Path(cand)
        if p.exists():
            return p.resolve()
    return Path(explicit or BASE_CANDIDATES[0]).resolve()


def resolve_dept(path: Path) -> str:
    for part in path.parts:
        for key, dept_id in DEPT_MAP.items():
            if key in part:
                return dept_id
    return "dept_all"


async def main(base: str) -> None:
    base_path = resolve_base(base or None)
    if not base_path.exists():
        print(f"目录不存在: {base_path}（可显式指定 --base，如 --base /app/department_files）")
        return

    settings = get_settings()
    container = build_container(settings)
    if container.mongo is not None:
        await container.mongo.connect()
    if hasattr(container.session_store, "connect"):
        try:
            await container.session_store.connect()
        except Exception:
            pass

    files = [p for p in base_path.rglob("*") if p.is_file() and p.suffix.lower() in SUFFIXES]
    print(f"发现 {len(files)} 个文档待导入")

    ok, fail = 0, 0
    for fp in files:
        dept_id = resolve_dept(fp)
        try:
            doc = await container.indexer.ingest(fp, dept_id=dept_id, uploaded_by="seed")
            await container.conflict_detector.run_for_document(doc)
            print(f"[ok] {fp.name} -> {dept_id} ({doc['chunk_count']} chunks)")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"[fail] {fp.name}: {exc}")
            fail += 1

    print(f"完成: 成功 {ok}，失败 {fail}")
    if container.mongo is not None:
        await container.mongo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="", help="示例文档根目录（默认自动探测 ../department_files 或 /app/department_files）")
    args = parser.parse_args()
    asyncio.run(main(args.base))
