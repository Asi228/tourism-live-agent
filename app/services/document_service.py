"""文档业务逻辑：上传 → 处理 → 查询 → 删除。

两条链路要分清：
  请求链路（upload_document / list / get / delete）：用接口层的 db session，
      请求结束就关闭，权限校验在这里做。
  处理链路（process_document）：跑在 BackgroundTasks 里，**请求早就结束了**，
      接口层那个 session 已经关闭，所以必须自己开一个 session。

权限口径全部复用 knowledge_service.ensure_kb_permission：
  上传 / 删除 → 需要对该知识库 write
  列表 / 详情 / chunk → 需要 read
write 档位在 Phase 3 就留好了（当时注释里写明是留给文档写入的），这里正式启用。
"""

import logging
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.db import session as db_session
from app.models.document import (
    DOCUMENT_TYPES,
    PRODUCT_SCOPED_DOCUMENT_TYPES,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    Document,
)
from app.models.document_chunk import DocumentChunk
from app.models.user import User
from app.rag import cleaner, loader, splitter
from app.repositories import document_chunk_repository, document_repository
from app.repositories import live_commerce_repository
from app.services import knowledge_service
from app.utils import file_storage

logger = logging.getLogger(__name__)

# 对外统一话术：区分"不存在"和"无权访问"等于把枚举能力还给攻击者
NO_ACCESS_MSG = "文档不存在或无权访问"


# ==================== 上传 ====================


async def upload_document(
    db: Session,
    user: User,
    kb_id: int,
    upload: UploadFile,
    *,
    product_id: int | None = None,
    sku_id: int | None = None,
    document_type: str = "general",
    version: str = "v1",
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
) -> Document:
    """保存文件并建一条 pending 记录。真正的解析由 process_document 在后台做。"""
    kb = knowledge_service.ensure_kb_permission(db, user, kb_id, knowledge_service.PERM_WRITE)

    doc_type = document_type.strip().lower()
    if doc_type not in DOCUMENT_TYPES:
        raise AppException(ErrorCode.PARAM_ERROR, "不支持的资料类型")
    doc_version = version.strip()
    if not doc_version:
        raise AppException(ErrorCode.PARAM_ERROR, "资料版本不能为空")
    if len(doc_version) > 32:
        raise AppException(ErrorCode.PARAM_ERROR, "资料版本最多 32 个字符")
    if effective_from and effective_to and effective_from > effective_to:
        raise AppException(ErrorCode.PARAM_ERROR, "生效时间不能晚于失效时间")

    product = live_commerce_repository.get_product(db, product_id) if product_id else None
    if product_id is not None and product is None:
        raise AppException(ErrorCode.NOT_FOUND, "关联商品不存在")
    sku = live_commerce_repository.get_sku(db, sku_id) if sku_id else None
    if sku_id is not None and sku is None:
        raise AppException(ErrorCode.NOT_FOUND, "关联 SKU 不存在")
    if sku is not None:
        if product_id is not None and sku.product_id != product_id:
            raise AppException(ErrorCode.PARAM_ERROR, "SKU 不属于所选商品")
        product_id = sku.product_id
    if doc_type in PRODUCT_SCOPED_DOCUMENT_TYPES and product_id is None:
        raise AppException(ErrorCode.PARAM_ERROR, "该资料类型必须关联商品或 SKU")

    saved = await file_storage.save_upload(upload, kb.id)
    try:
        doc = document_repository.create(
            db,
            knowledge_base_id=kb.id,
            uploader_id=user.id,
            filename=file_storage.display_filename(upload.filename or saved.stored_name),
            stored_name=saved.stored_name,
            file_path=str(saved.file_path),
            file_type=saved.file_type,
            file_size=saved.file_size,
            product_id=product_id,
            sku_id=sku_id,
            document_type=doc_type,
            version=doc_version,
            effective_from=effective_from,
            effective_to=effective_to,
        )
        db.commit()
    except Exception:
        # 记录没入库，落盘的文件就得清掉，否则会攒下一堆没人引用的垃圾文件
        db.rollback()
        file_storage.delete_file(saved.file_path)
        raise

    logger.info(
        "上传文档 doc_id=%s kb_id=%s filename=%s size=%s by=%s",
        doc.id, kb.id, doc.filename, doc.file_size, user.username,
    )
    return doc


# ==================== 处理（后台任务）====================


def _mark_failed(db: Session, doc: Document, message: str) -> None:
    """把文档标记为失败。先 rollback，保证半截事务不会把状态一起回滚掉。"""
    db.rollback()
    doc.status = STATUS_FAILED
    doc.error_msg = message
    doc.chunk_count = 0
    db.commit()


def process_document(document_id: int) -> None:
    """解析 → 清洗 → 切分 → 入库。**必须在后台执行**，会自己开 session。

    设计要点：
    - session 统一走 `db_session.new_session()` —— 它就是一个**可替换点**
      （`app.db.session.new_session`），测试换那一处，后台任务就会写进 SQLite
      而不是真库。以前这里用的是模块级 `SessionLocal`，于是"要 patch 的地方"
      凭空多了一处（漏掉它的后果不是测试变红，而是**静默写真库**）。
    - 任何异常都不往外抛：后台任务的异常没人接，抛出去只会变成日志里一行 traceback，
      而文档会永远卡在 processing。这里全部吃掉并落到 status=failed + error_msg。
    """
    with db_session.new_session() as db:
        doc = document_repository.get_by_id(db, document_id)
        if doc is None:
            logger.warning("文档不存在，跳过处理 doc_id=%s", document_id)
            return

        doc.status = STATUS_PROCESSING
        doc.error_msg = None
        db.commit()

        try:
            pages = loader.load(Path(doc.file_path), doc.file_type)
            cleaned = cleaner.clean_pages(pages)
            if not cleaned:
                raise AppException(ErrorCode.FILE_ERROR, "文档清洗后没有剩余内容")

            chunks = splitter.split_pages(
                cleaned,
                chunk_size=settings.CHUNK_SIZE,
                chunk_overlap=settings.CHUNK_OVERLAP,
            )
            if not chunks:
                raise AppException(ErrorCode.FILE_ERROR, "文档切分后没有产生任何内容块")

            # 先删后插：让"处理"可以重复执行（重跑不会撞唯一约束）
            #
            # 旧 chunk 被换掉的同时，对应的向量也必须一起作废：
            # Milvus 只按主键（chunk_id）upsert，不会自动清走旧行，
            # 重跑一次就会留下一批指向旧 chunk_id 的孤儿向量 ——
            # 它们检索得到、却在 MySQL 里找不到正文，白占 top_k 名额。
            # 用 _purge_vectors 而不是直接调 vector_store：它吞异常，
            # 因为 Milvus 不可用不应该让"文档处理"这个功能整个失败；
            # 真漏掉的向量在下次向量化时会按 document_id 兜底清掉（双层防御）。
            _purge_vectors(doc.id)
            document_chunk_repository.delete_by_document(db, doc.id)
            document_chunk_repository.bulk_create(db, doc.id, chunks)

            doc.chunk_count = len(chunks)
            doc.char_count = sum(len(text) for _, _, text in chunks)
            doc.status = STATUS_COMPLETED
            doc.error_msg = None
            db.commit()
            logger.info(
                "文档处理完成 doc_id=%s chunks=%s chars=%s",
                doc.id, doc.chunk_count, doc.char_count,
            )
        except AppException as e:
            # 业务异常：message 是写给用户看的，原样落库
            logger.warning("文档处理失败 doc_id=%s code=%s msg=%s", doc.id, e.code, e.message)
            _mark_failed(db, doc, e.message)
        except Exception:
            # 未预期异常：完整堆栈只进日志，不落库（避免把绝对路径/SQL 细节返回给客户端）
            logger.exception("文档处理出现未预期异常 doc_id=%s", doc.id)
            _mark_failed(db, doc, "文档处理失败，请查看服务端日志")


# ==================== 查询 ====================


def list_documents(
    db: Session,
    user: User,
    kb_id: int | None = None,
    *,
    product_id: int | None = None,
    sku_id: int | None = None,
    document_type: str | None = None,
    active_only: bool = False,
) -> list[Document]:
    """列文档。

    传 kb_id：先校验对该库的 read 权限，再列这个库的文档。
    不传 kb_id：列出**所有可访问知识库**的文档。

    这里第二次用到 get_accessible_kb_ids() —— 和知识库列表同一套口径。
    Phase 5 的 Milvus 检索过滤也会用它，三处共用一份权限计算，
    不会出现"列表过滤了、检索没过滤"这种漏洞。
    """
    if kb_id is not None:
        knowledge_service.ensure_kb_permission(db, user, kb_id, knowledge_service.PERM_READ)
        scope = [kb_id]
    else:
        scope = knowledge_service.get_accessible_kb_ids(db, user)

    doc_type = document_type.strip().lower() if document_type else None
    if doc_type is not None and doc_type not in DOCUMENT_TYPES:
        raise AppException(ErrorCode.PARAM_ERROR, "不支持的资料类型")
    return document_repository.list_filtered(
        db,
        kb_ids=scope,
        product_id=product_id,
        sku_id=sku_id,
        document_type=doc_type,
        active_at=datetime.now() if active_only else None,
    )



def get_document(db: Session, user: User, document_id: int) -> Document:
    doc = document_repository.get_by_id(db, document_id)
    if doc is None:
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)
    _ensure_doc_read(db, user, doc)
    return doc


def _ensure_doc_read(db: Session, user: User, doc: Document) -> None:
    """文档权限 = 它所属知识库的权限。没有独立于知识库的文档权限。"""
    try:
        knowledge_service.ensure_kb_permission(
            db, user, doc.knowledge_base_id, knowledge_service.PERM_READ
        )
    except AppException:
        # 把知识库的"不存在/无权限"换成文档口径，避免从 message 反推出知识库是否存在
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)


def get_chunk_with_document(
    db: Session, user: User, chunk_id: int
) -> tuple[DocumentChunk, Document]:
    """按 id 取一个切分块 + 它所属的文档（**引用溯源**用）。

    点答案里的 `[1]` 要看到"那段原文出自哪份文档的哪一页"，所以块和文档
    一起返回 —— 让前端再发一个文档详情请求既慢又多一次越权面。

    ⚠️ 权限 = 文档所属知识库的读权限（没有独立于知识库的文档权限）。
    没权限与不存在**共用同一个 404**，不泄露"这个 chunk_id 存在"。
    """
    chunk = document_chunk_repository.get_by_id(db, chunk_id)
    if chunk is None:
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)
    doc = get_document(db, user, chunk.document_id)
    return chunk, doc


def list_chunks(db: Session, user: User, document_id: int):
    doc = get_document(db, user, document_id)
    return document_chunk_repository.list_by_document(db, doc.id)


# ==================== 删除 ====================


def delete_document(db: Session, user: User, document_id: int) -> None:
    doc = document_repository.get_by_id(db, document_id)
    if doc is None:
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)

    try:
        knowledge_service.ensure_kb_permission(
            db, user, doc.knowledge_base_id, knowledge_service.PERM_WRITE
        )
    except AppException:
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)

    file_path = doc.file_path
    filename = doc.filename
    document_repository.delete(db, doc)  # chunk 由 cascade 一起删
    db.commit()

    # 数据库删成功之后再删磁盘文件：反过来的话，文件删了而事务回滚，
    # 就会留下一条指向不存在文件的记录，点开就报错
    file_storage.delete_file(file_path)
    file_storage.remove_dir_if_empty(Path(file_path).parent)
    _purge_vectors(document_id)
    logger.info("删除文档 doc_id=%s filename=%s by=%s", document_id, filename, user.username)


def _purge_vectors(document_id: int) -> None:
    """尽力清掉 Milvus 里属于这份文档的向量。

    **局部 import 是故意的**：文档删除这条链路不该依赖 Milvus。
    数据库记录和磁盘文件都已经删掉了，此时向量库连不上只能说明它现在不可用，
    不该让整个删除接口失败（否则会出现"删不掉的文档"）。
    所以这里吃掉所有异常，只记 warning。
    """
    try:
        from app.rag import vector_store

        removed = vector_store.delete_by_document(document_id)
        if removed:
            # 删除同样要落盘：不落盘的删除记录在服务端重启后可能没生效 ——
            # 已删文档的向量会"复活"，变成命中却取不到正文的孤儿向量
            logger.info("已清理向量 doc_id=%s count=%s flushed=%s",
                        document_id, removed, vector_store.flush())
    except Exception as e:  # noqa: BLE001
        logger.warning("清理向量失败（不影响文档删除）doc_id=%s err=%s", document_id, e)
