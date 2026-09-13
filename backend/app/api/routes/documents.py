"""文档入库与管理接口（管理员；部门管理员仅限本部门）。"""
from __future__ import annotations
from typing import Optional

import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile

from app.api.deps import require_admin, scope_dept
from app.api.schemas import ApiResponse, DocumentStatusUpdate
from app.utils.logging import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])

# 允许的文档类型（与 pipeline/parser.py 支持的范围一致）
ALLOWED_EXTENSIONS = {".pdf", ".docx", ".md", ".markdown", ".html", ".htm", ".txt"}
_CHUNK = 1024 * 1024  # 1MB


@router.post("/upload", response_model=ApiResponse)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    dept_id: str = Form(...),
    uploaded_by: str = Form(""),
    user: dict = Depends(require_admin),
):
    container = request.app.state.container
    scope = scope_dept(user)
    if scope and dept_id != scope:
        raise HTTPException(status_code=403, detail="部门管理员只能向本部门上传文档")

    # 1) 扩展名白名单
    original_name = Path(file.filename or "doc").name or "doc.txt"
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型 {suffix or '(无扩展名)'}，仅支持 {sorted(ALLOWED_EXTENSIONS)}")

    # 2) 大小限制（读入时即截止，避免超限文件占满磁盘）
    max_bytes = container.settings.max_upload_mb * 1024 * 1024
    upload_dir = Path(container.settings.upload_storage_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    job_dir = upload_dir / uuid.uuid4().hex
    job_dir.mkdir(parents=True, exist_ok=True)
    stored_path = job_dir / original_name
    total = 0
    try:
        with open(stored_path, "wb") as f:
            while True:
                chunk = await file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过大小上限 {container.settings.max_upload_mb}MB",
                    )
                f.write(chunk)
        job = await container.job_queue.enqueue("ingest_document", {
            "path": str(stored_path), "original_name": original_name,
            "dept_id": dept_id, "uploaded_by": user["id"],
        })
        return ApiResponse(data={"queued": True, "job_id": job["_id"], "file_name": original_name})
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        logger.exception("文档入库失败 (file=%s, dept=%s)", original_name, dept_id)
        stored_path.unlink(missing_ok=True)
        job_dir.rmdir()
        # 不向客户端回显内部异常细节
        return ApiResponse(code=1, message="入库失败，请检查文件格式后重试")
    finally:
        await file.close()


@router.get("/jobs/{job_id}", response_model=ApiResponse)
async def get_upload_job(job_id: str, request: Request, user: dict = Depends(require_admin)):
    job = await request.app.state.container.store.get("async_jobs", job_id)
    if not job or (job.get("payload") or {}).get("uploaded_by") != user["id"]:
        raise HTTPException(status_code=404, detail="作业不存在")
    return ApiResponse(data=job)


@router.get("", response_model=ApiResponse)
async def list_documents(
    request: Request,
    dept_id: Optional[str] = None,
    status: Optional[str] = None,
    user: dict = Depends(require_admin),
):
    container = request.app.state.container
    scope = scope_dept(user)
    if scope:
        dept_id = scope  # 部门管理员只看本部门文档
    docs = await container.store.list_documents(dept_id=dept_id, status=status)
    return ApiResponse(data=docs)


@router.get("/{doc_id}", response_model=ApiResponse)
async def get_document(doc_id: str, request: Request, user: dict = Depends(require_admin)):
    container = request.app.state.container
    doc = await container.store.get_document(doc_id)
    if doc is None:
        return ApiResponse(code=1, message="文档不存在")
    scope = scope_dept(user)
    if scope and doc.get("dept_id") != scope:
        raise HTTPException(status_code=403, detail="无权访问其它部门的文档")
    chunks = await container.store.list_chunks_by_doc(doc_id)
    doc["chunks"] = chunks
    return ApiResponse(data=doc)


@router.post("/{doc_id}/status", response_model=ApiResponse)
async def update_status(doc_id: str, body: DocumentStatusUpdate, request: Request, user: dict = Depends(require_admin)):
    container = request.app.state.container
    doc = await container.store.get_document(doc_id)
    if doc is None:
        return ApiResponse(code=1, message="文档不存在")
    scope = scope_dept(user)
    if scope and doc.get("dept_id") != scope:
        raise HTTPException(status_code=403, detail="无权操作其它部门的文档")
    allowed = {"draft", "review", "active", "archived", "deleted"}
    if body.status not in allowed:
        raise HTTPException(status_code=400, detail="非法文档状态")
    await container.indexer.set_status(doc_id, body.status)
    return ApiResponse(data={"doc_id": doc_id, "status": body.status})
