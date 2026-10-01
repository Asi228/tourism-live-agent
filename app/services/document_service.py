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
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.db.session import SessionLocal
from app.models.document import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PROCESSING,
    Document,
)
from app.models.user import User
from app.rag import cleaner, loader, splitter
from app.repositories import document_chunk_repository, document_repository
from app.services import knowledge_service
from app.utils import file_storage

logger = logging.getLogger(__name__)

# 对外统一话术：区分"不存在"和"无权访问"等于把枚举能力还给攻击者
NO_ACCESS_MSG = "文档不存在或无权访问"


# ==================== 上传 ====================


async def upload_document(
    db: Session, user: User, kb_id: int, upload: UploadFile
) -> Document:
    """保存文件并建一条 pending 记录。真正的解析由 process_document 在后台做。"""
    kb = knowledge_service.ensure_kb_permission(db, user, kb_id, knowledge_service.PERM_WRITE)

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
    - 用模块级 SessionLocal（而不是在函数里 from app.db.session import ...），
      这样测试可以 monkeypatch 掉它，让后台任务写进 SQLite 而不是真库。
    - 任何异常都不往外抛：后台任务的异常没人接，抛出去只会变成日志里一行 traceback，
      而文档会永远卡在 processing。这里全部吃掉并落到 status=failed + error_msg。
    """
    with SessionLocal() as db:
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
    db: Session, user: User, kb_id: int | None = None
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
        return document_repository.list_by_kb(db, kb_id)

    allowed = knowledge_service.get_accessible_kb_ids(db, user)
    if allowed is None:
        # None = 系统管理员，不受限
        return document_repository.list_all(db)
    return document_repository.list_by_kb_ids(db, allowed)



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
    logger.info("删除文档 doc_id=%s filename=%s by=%s", document_id, filename, user.username)
