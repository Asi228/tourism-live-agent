"""FAQ 接口（常见问题置顶）。

  GET    /api/faqs?kb_id=N     某库的 FAQ（有 read 权限即可）
  POST   /api/faqs             新增（需要库的 admin 权限）
  PATCH  /api/faqs/{id}        修改
  DELETE /api/faqs/{id}        删除

**为什么用 `?kb_id=` 而不是 `/knowledge/{kb_id}/faqs`**：读场景里 kb_id
常是可选的（问答页可能"检索范围是全部有权限的库"），查询参数更贴切；
写场景由 body 携带 kb_id，改动的归属一眼可见。

⚠️ **FAQ 的答案不进向量库、不参与检索** —— 它是"命中即返回"的快捷方式，
与 RAG 两条独立的路，混进来会让"这个答案来自哪份文档"说不清。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.faq import FaqCreate, FaqOut, FaqUpdate
from app.services import faq_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/faqs", tags=["常见问题"])


@router.get(
    "",
    response_model=ApiResponse[list[FaqOut]],
    summary="某知识库的常见问题（有读权限即可）",
)
def list_faqs(
    kb_id: int = Query(ge=1, description="知识库 id"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """问答页用它渲染顶部的 FAQ 卡片。没有权限一律 404。"""
    rows = faq_service.list_faqs(db, current_user, kb_id)
    return success([FaqOut.model_validate(r) for r in rows])


@router.post(
    "", response_model=ApiResponse[FaqOut], summary="新增一条常见问题（需库管理权限）"
)
def create_faq(
    payload: FaqCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = faq_service.create_faq(
        db, current_user,
        kb_id=payload.knowledge_base_id,
        question=payload.question,
        answer=payload.answer,
        sort_order=payload.sort_order,
    )
    return success(FaqOut.model_validate(row), "已添加")


@router.patch(
    "/{faq_id}", response_model=ApiResponse[FaqOut], summary="修改一条常见问题"
)
def update_faq(
    faq_id: int,
    payload: FaqUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = faq_service.update_faq(
        db, current_user, faq_id,
        question=payload.question,
        answer=payload.answer,
        sort_order=payload.sort_order,
    )
    return success(FaqOut.model_validate(row), "已更新")


@router.delete("/{faq_id}", summary="删除一条常见问题")
def delete_faq(
    faq_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    faq_service.delete_faq(db, current_user, faq_id)
    return success(None, "已删除")
