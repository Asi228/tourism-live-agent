"""文档切分块数据访问。"""

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.document_chunk import DocumentChunk


def list_by_document(db: Session, document_id: int) -> list[DocumentChunk]:
    stmt = (
        select(DocumentChunk)
        .where(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
    )
    return list(db.scalars(stmt))


def bulk_create(
    db: Session,
    document_id: int,
    items: list[tuple[int, int | None, str]],
) -> int:
    """批量插入 chunk。items = [(chunk_index, page_number, content), ...]

    用 add_all + flush 而不是逐条 add：一份文档几百块，
    逐条 add 会触发几百次 flush 检查，明显更慢。
    """
    if not items:
        return 0
    db.add_all(
        [
            DocumentChunk(
                document_id=document_id,
                chunk_index=index,
                page_number=page_number,
                content=content,
                char_count=len(content),
            )
            for index, page_number, content in items
        ]
    )
    db.flush()
    return len(items)


def delete_by_document(db: Session, document_id: int) -> int:
    """删除某文档的全部 chunk。

    重新处理前先清空，让"处理"变成幂等操作 ——
    否则重跑一次就会撞上 uq_doc_chunk_index 唯一约束。
    """
    result = db.execute(
        sa_delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
    )
    db.flush()
    return result.rowcount or 0
