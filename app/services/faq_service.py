"""FAQ 的编排（常见问题置顶）。

权限口径与知识库其他子资源一致：
  · 读（列表）= 库的 **read** 权限
  · 写（增删改）= 库的 **admin** 权限
无权与不存在共用 404，不泄露"这个 id 存在"。

⚠️ **FAQ 的答案刻意不进向量库、不参与检索**：它是"命中即返回"的快捷方式，
与 RAG 是两条独立的路。混进来会让"这个答案来自哪份文档"说不清 ——
而可溯源正是这套系统最值钱的地方。
"""

import logging

from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.knowledge_base_faq import (
    ANSWER_MAX_LENGTH,
    FAQ_MAX_PER_KB,
    QUESTION_MAX_LENGTH,
    KnowledgeBaseFaq,
)
from app.models.user import User
from app.repositories import faq_repository
from app.services import knowledge_service

logger = logging.getLogger(__name__)


def _require_admin(db: Session, user: User, kb_id: int):
    """把"库的 admin 权限"转成 404 语义（不是 403）。"""
    try:
        return knowledge_service.ensure_kb_permission(
            db, user, kb_id, knowledge_service.PERM_ADMIN
        )
    except AppException:
        raise AppException(ErrorCode.NOT_FOUND, "知识库不存在或没有管理权限")


def _require_read(db: Session, user: User, kb_id: int):
    try:
        return knowledge_service.ensure_kb_permission(
            db, user, kb_id, knowledge_service.PERM_READ
        )
    except AppException:
        raise AppException(ErrorCode.NOT_FOUND, "知识库不存在或没有访问权限")


def list_faqs(db: Session, user: User, kb_id: int) -> list[KnowledgeBaseFaq]:
    """某库的 FAQ（有读权限即可）。"""
    _require_read(db, user, kb_id)
    return faq_repository.list_by_kb(db, kb_id)


def create_faq(
    db: Session,
    user: User,
    kb_id: int,
    question: str,
    answer: str,
    sort_order: int = 0,
) -> KnowledgeBaseFaq:
    """新增一条 FAQ。超过 `FAQ_MAX_PER_KB` 直接拒绝，不静默挤掉旧的。"""
    _require_admin(db, user, kb_id)

    q = (question or "").strip()
    a = (answer or "").strip()
    if not q:
        raise AppException(ErrorCode.PARAM_ERROR, "问题不能为空")
    if not a:
        raise AppException(ErrorCode.PARAM_ERROR, "答案不能为空")
    if len(q) > QUESTION_MAX_LENGTH:
        raise AppException(
            ErrorCode.PARAM_ERROR, f"问题最多 {QUESTION_MAX_LENGTH} 字"
        )
    if len(a) > ANSWER_MAX_LENGTH:
        raise AppException(
            ErrorCode.PARAM_ERROR, f"答案最多 {ANSWER_MAX_LENGTH} 字"
        )

    if faq_repository.count_by_kb(db, kb_id) >= FAQ_MAX_PER_KB:
        raise AppException(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"一个知识库最多 {FAQ_MAX_PER_KB} 条常见问题，请先删除一些",
        )

    row = faq_repository.create(db, kb_id, q, a, sort_order, user.id)
    db.commit()
    logger.info("新增 FAQ kb=%s id=%s user=%s", kb_id, row.id, user.username)
    return row


def update_faq(
    db: Session,
    user: User,
    faq_id: int,
    question: str | None = None,
    answer: str | None = None,
    sort_order: int | None = None,
) -> KnowledgeBaseFaq:
    """改一条 FAQ。三个字段都可选，但**至少要给一个**（同 `MemoryUpdate`）。"""
    if question is None and answer is None and sort_order is None:
        raise AppException(ErrorCode.PARAM_ERROR, "没有需要修改的内容")

    row = faq_repository.get_by_id(db, faq_id)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "常见问题不存在")
    _require_admin(db, user, row.knowledge_base_id)

    if question is not None:
        q = (question or "").strip()
        if not q:
            raise AppException(ErrorCode.PARAM_ERROR, "问题不能为空")
        if len(q) > QUESTION_MAX_LENGTH:
            raise AppException(
                ErrorCode.PARAM_ERROR, f"问题最多 {QUESTION_MAX_LENGTH} 字"
            )
        row.question = q
    if answer is not None:
        a = (answer or "").strip()
        if not a:
            raise AppException(ErrorCode.PARAM_ERROR, "答案不能为空")
        if len(a) > ANSWER_MAX_LENGTH:
            raise AppException(
                ErrorCode.PARAM_ERROR, f"答案最多 {ANSWER_MAX_LENGTH} 字"
            )
        row.answer = a
    if sort_order is not None:
        row.sort_order = sort_order

    db.commit()
    logger.info("修改 FAQ id=%s user=%s", faq_id, user.username)
    return row


def delete_faq(db: Session, user: User, faq_id: int) -> None:
    """删一条 FAQ。**不可逆**。"""
    row = faq_repository.get_by_id(db, faq_id)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "常见问题不存在")
    _require_admin(db, user, row.knowledge_base_id)

    db.delete(row)
    db.commit()
    logger.info("删除 FAQ id=%s user=%s", faq_id, user.username)
