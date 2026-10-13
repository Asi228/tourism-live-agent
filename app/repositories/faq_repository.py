"""FAQ 的数据访问。口径同其余 repository：只 flush，不 commit。"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.knowledge_base_faq import KnowledgeBaseFaq

logger = logging.getLogger(__name__)


def list_by_kb(db: Session, kb_id: int) -> list[KnowledgeBaseFaq]:
    """某库的 FAQ 列表，`sort_order` 小的在前。

    次键用 `id`（不是 updated_at）：排序值被写成一样时（默认值 0 很常见），
    只按 sort_order 会得到不确定顺序，界面上就会看到条目自己换位置。
    """
    return list(
        db.scalars(
            select(KnowledgeBaseFaq)
            .where(KnowledgeBaseFaq.knowledge_base_id == kb_id)
            .order_by(KnowledgeBaseFaq.sort_order, KnowledgeBaseFaq.id)
        )
    )


def count_by_kb(db: Session, kb_id: int) -> int:
    """该库现有多少条 FAQ（上限判断用）。"""
    return db.scalar(
        select(func.count())
        .select_from(KnowledgeBaseFaq)
        .where(KnowledgeBaseFaq.knowledge_base_id == kb_id)
    ) or 0


def get_by_id(db: Session, faq_id: int) -> KnowledgeBaseFaq | None:
    """按主键取一条。**不做归属判断** —— 权限由 service 层按它所属库判定。"""
    return db.get(KnowledgeBaseFaq, faq_id)


def create(
    db: Session,
    kb_id: int,
    question: str,
    answer: str,
    sort_order: int,
    created_by: int,
) -> KnowledgeBaseFaq:
    row = KnowledgeBaseFaq(
        knowledge_base_id=kb_id,
        question=question,
        answer=answer,
        sort_order=sort_order,
        created_by=created_by,
    )
    db.add(row)
    db.flush()
    return row
