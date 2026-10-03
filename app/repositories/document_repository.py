"""文档数据访问。只负责查/写数据库，不做权限判断。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document import STATUS_COMPLETED, STATUS_FAILED, Document


def get_by_id(db: Session, document_id: int) -> Document | None:
    return db.get(Document, document_id)


def list_by_kb(db: Session, kb_id: int) -> list[Document]:
    stmt = (
        select(Document)
        .where(Document.knowledge_base_id == kb_id)
        .order_by(Document.id.desc())
    )
    return list(db.scalars(stmt))


def list_all(db: Session) -> list[Document]:
    """仅系统管理员路径使用（他要看的是全部，不受知识库授权限制）。"""
    return list(db.scalars(select(Document).order_by(Document.id.desc())))


def get_by_ids(db: Session, document_ids: list[int]) -> list[Document]:
    """按 id 批量取（检索结果要显示文件名，用它一次取完，避免 N+1）。"""
    if not document_ids:
        return []
    return list(db.scalars(select(Document).where(Document.id.in_(document_ids))))


def list_by_kb_ids(db: Session, kb_ids: list[int]) -> list[Document]:
    """按知识库 id 列表查（空列表直接返回，避免生成 IN () 这种非法 SQL）。"""
    if not kb_ids:
        return []
    stmt = (
        select(Document)
        .where(Document.knowledge_base_id.in_(kb_ids))
        .order_by(Document.id.desc())
    )
    return list(db.scalars(stmt))


def list_file_paths_by_kb(db: Session, kb_id: int) -> list[str]:
    """某个知识库下全部文档的磁盘路径。

    给"删知识库要连带清文件"用：必须在删 Document 行之前调，
    行删掉之后就再也查不出路径了。
    """
    stmt = select(Document.file_path).where(Document.knowledge_base_id == kb_id)
    return list(db.scalars(stmt))


def create(
    db: Session,
    *,
    knowledge_base_id: int,
    uploader_id: int,
    filename: str,
    stored_name: str,
    file_path: str,
    file_type: str,
    file_size: int,
) -> Document:
    """只 flush 不 commit —— 事务边界归 Service 层（全项目统一约定）。"""
    doc = Document(
        knowledge_base_id=knowledge_base_id,
        uploader_id=uploader_id,
        filename=filename,
        stored_name=stored_name,
        file_path=file_path,
        file_type=file_type,
        file_size=file_size,
    )
    db.add(doc)
    db.flush()
    return doc


def delete(db: Session, doc: Document) -> None:
    """删除文档，chunk 由 ORM cascade 一起删。"""
    db.delete(doc)
    db.flush()


def list_unfinished(db: Session) -> list[Document]:
    """还没处理完的文档（pending / processing）。

    启动时用它兜底：进程中途被杀会留下永远停在 processing 的记录，
    没有这个查询就没人会发现它们。Phase 5 之后可接任务队列进一步处理。
    """
    stmt = select(Document).where(
        Document.status.notin_([STATUS_COMPLETED, STATUS_FAILED])
    )
    return list(db.scalars(stmt))
