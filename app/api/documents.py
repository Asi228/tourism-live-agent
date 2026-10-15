"""文档接口。

权限矩阵（全部继承自所属知识库的权限，文档没有独立权限）：
  POST   /api/documents/upload           对该库 write
  GET    /api/documents?kb_id=           对该库 read（不传 kb_id 则列出所有可访问库的）
  GET    /api/documents/{id}             对该库 read
  GET    /api/documents/{id}/status      对该库 read（前端轮询用）
  GET    /api/documents/{id}/chunks      对该库 read
  POST   /api/documents/{id}/vectorize   对该库 write（把切分块写入 Milvus）
  DELETE /api/documents/{id}             对该库 write

上传走 BackgroundTasks：接口立刻返回（status=pending），解析在后台做。
这是需求文档 §10 的 MVP 方案 —— 不引入 Celery/RQ 也能不阻塞请求。

向量化（Embedding + 写 Milvus）是**单独一步**，不塞进后台解析任务里：
它依赖外部服务（硅基流动 API + VM 上的 Milvus），挂掉一个不该让上传功能整体不可用。
"""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, rate_limit
from app.config import settings
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.document import ChunkOut, DocumentOut, DocumentStatusOut, UploadConfigOut
from app.schemas.search import VectorizeResult
from app.services import document_service, vector_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/documents", tags=["文档"])


def _doc_out(doc) -> dict:
    return DocumentOut.model_validate(doc).model_dump(mode="json")


@router.post("/upload", summary="上传文档（需对该知识库有 write 权限）")
async def upload_document(
    background_tasks: BackgroundTasks,
    kb_id: int = Form(..., description="目标知识库 id"),
    file: UploadFile = File(..., description="pdf / txt / md / markdown / docx"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = await document_service.upload_document(db, current_user, kb_id, file)

    # 注意：后台任务拿的是 document_id 而不是 ORM 对象 —— 请求结束后这个 session 就关了，
    # 对象会处于 detached 状态。后台任务自己开新 session 重新查一次。
    background_tasks.add_task(document_service.process_document, doc.id)

    return success(_doc_out(doc), "上传成功，正在后台解析")


@router.get("", summary="文档列表")
def list_documents(
    kb_id: int | None = Query(default=None, description="不传则返回所有可访问知识库的文档"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    docs = document_service.list_documents(db, current_user, kb_id)
    return success([_doc_out(d) for d in docs])


@router.get(
    "/upload-config",
    response_model=ApiResponse[UploadConfigOut],
    summary="上传约束（前端取 accept 白名单用）",
)
def get_upload_config(current_user: User = Depends(get_current_user)):
    """把 `.env` 的 `ALLOWED_EXTENSIONS` 下发给前端。

    前端曾经在 Documents.vue 里硬编码了同一份清单（"第 4 份清单"）—— 改了 .env 之后，
    界面会继续放出一个服务端必拒的格式（2026-10-15 评审指出）。现在白名单只有
    后端这一处权威，前端从本接口取。

    ⚠️ 本路由必须注册在 `/{document_id}` **之前** —— 否则 "upload-config" 会被
    当成文档 id 去走校验，报 422 而不是走到这里。
    """
    return success({"allowed_extensions": sorted(settings.allowed_extensions_set)})


@router.get("/{document_id}", summary="文档详情")
def get_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = document_service.get_document(db, current_user, document_id)
    return success(_doc_out(doc))


@router.get("/{document_id}/status", summary="文档处理状态（轮询用）")
def get_document_status(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    doc = document_service.get_document(db, current_user, document_id)
    status = DocumentStatusOut(
        id=doc.id, status=doc.status, chunk_count=doc.chunk_count, error_msg=doc.error_msg
    )
    return success(status.model_dump(mode="json"))


@router.get(
    "/{document_id}/chunks",
    response_model=ApiResponse[list[ChunkOut]],
    summary="查看切分结果（含页码，用于核对切分效果）",
)
def list_chunks(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    chunks = document_service.list_chunks(db, current_user, document_id)
    # 走 ChunkOut 而不是手写 dict：字段增删只在 schema 里改一处，响应不会和 schema 漂移
    return success([ChunkOut.model_validate(c).model_dump(mode="json") for c in chunks])


@router.post(
    "/{document_id}/vectorize",
    response_model=ApiResponse[VectorizeResult],
    summary="把该文档的切分块向量化并写入 Milvus",
)
def vectorize_document(
    document_id: int,
    _: None = Depends(rate_limit("vectorize")),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = vector_service.vectorize_document(db, current_user, document_id)
    # 写成功但没落盘 ≠ 没事：重启后那批向量对检索就是不可见的，必须当场说出来
    tail = "" if data["flushed"] else "（未落盘，重启后可能检索不到，请到「知识库」的向量库自检查看）"
    return success(data, f"向量化完成，写入 {data['vectorized']} 条{tail}")


@router.delete("/{document_id}", summary="删除文档（需对该知识库有 write 权限）")
def delete_document(
    document_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    document_service.delete_document(db, current_user, document_id)
    return success(None, "删除成功")
