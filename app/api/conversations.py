"""会话接口（Phase 9）。

  GET    /api/conversations          我的会话列表（带消息数，支持 limit/offset 翻页）
  GET    /api/conversations/{id}     会话详情（含全部消息，用于还原界面）
  DELETE /api/conversations/{id}     删除会话及其消息

**没有"创建会话"接口**：会话是首次提问时由后端隐式建的（见 conversation_service），
前端直接开问就行，少一步交互也少一个可能失败的请求。

归属校验在 service 层：拿到别人的 id 一律报 404「会话不存在」，而不是 403 ——
403 等于确认了"这个 id 存在，只是不归你"，白送一条可枚举的信息。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import success
from app.db.session import get_db
from app.models.user import User
from app.schemas.conversation import ConversationDetail, ConversationItem, MessageOut
from app.services import conversation_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["会话"])


@router.get("", summary="我的会话列表（最近活跃在前，支持翻页）")
def list_conversations(
    # 上限 100：这个接口是本项目唯一能列出"更早会话"的入口，配再大也只是拉慢响应；
    # 真要看历史对话应当走详情接口，而不是把列表当全量导出。
    limit: int = Query(default=50, ge=1, le=100, description="每页条数，最多 100"),
    offset: int = Query(default=0, ge=0, description="跳过前 N 条，用于翻页"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = conversation_service.list_conversations(
        db, current_user, limit=limit, offset=offset
    )
    data = [
        ConversationItem(
            id=conv.id,
            title=conv.title,
            message_count=count,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        ).model_dump(mode="json")
        for conv, count in rows
    ]
    return success(data)


@router.get("/{conversation_id}", summary="会话详情（含全部消息，按当前权限脱敏）")
def get_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 归属校验走 get_owned（别人的会话一律 404）。消息**不再直接序列化 ORM 对象**，
    # 而是过一遍 load_messages_for_display：引用了"此刻已无权访问"的知识库的答案
    # 会被整条隐藏（撤销授权不能通过历史回放被绕开）。
    conv = conversation_service.get_owned(db, current_user, conversation_id)
    messages = conversation_service.load_messages_for_display(db, current_user, conv)
    detail = ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        messages=[MessageOut.model_validate(m) for m in messages],
    )
    return success(detail.model_dump(mode="json"))


@router.delete("/{conversation_id}", summary="删除会话（连带它的全部消息）")
def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation_service.delete_conversation(db, current_user, conversation_id)
    return success(None, "已删除")
