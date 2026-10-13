"""会话接口（Phase 9）。

  GET    /api/conversations          我的会话列表（带消息数，支持 limit/offset 翻页）
  GET    /api/conversations/{id}     会话详情（含全部消息，用于还原界面）
  DELETE /api/conversations/{id}     删除会话及其消息

**没有"创建会话"接口**：会话是首次提问时由后端隐式建的（见 conversation_service），
前端直接开问就行，少一步交互也少一个可能失败的请求。

归属校验在 service 层：拿到别人的 id 一律报 404「会话不存在」，而不是 403 ——
403 等于确认了"这个 id 存在，只是不归你"，白送一条可枚举的信息。

响应结构用 `response_model=ApiResponse[...]` 显式声明。这两个读接口是最后补的
（曾经全项目只有它们俩是 0），原因是它们的返回形状最"松"：`MessageOut.meta` 是
自由 dict、`sources` 是可变长列表，看着最容易在加字段时静默丢东西。
但实测确认可以安全补：现有代码本来就走 `MessageOut.model_validate()` +
`model_dump()`，输出早已被 schema 约束，加 response_model 是**纯增量**
（补 OpenAPI 文档 + 校验），不改变任何一个字节的响应体。
DELETE 刻意不声明 —— 它只返回 `success(None, "已删除")`，没有可声明的 data。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.conversation import ConversationDetail, ConversationItem, MessageOut
from app.services import conversation_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/conversations", tags=["会话"])


@router.get(
    "",
    response_model=ApiResponse[list[ConversationItem]],
    summary="我的会话列表（最近活跃在前，支持翻页）",
)
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
    # 声明了 response_model 之后不再需要手工 model_dump(mode="json")：
    # FastAPI 会按 ConversationItem 校验并序列化，两边结果是同一份。
    data = [
        ConversationItem(
            id=conv.id,
            title=conv.title,
            message_count=count,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
        )
        for conv, count in rows
    ]
    return success(data)


@router.get(
    "/{conversation_id}",
    response_model=ApiResponse[ConversationDetail],
    summary="会话详情（默认返回最新一页消息，可往前翻）",
)
def get_conversation(
    conversation_id: int,
    # 默认只返回**最新**一页：长会话一次全量返回会把 sources/meta 三段 JSON 全吐出去，
    # 响应又大又慢。往前翻用 keyset（before_id），不是 offset —— 会话尾部一直在长，
    # offset 会在翻页期间漏条/重条（理由见 message_repository.page_by_conversation）。
    limit: int = Query(default=50, ge=1, le=200, description="本页最多返回多少条消息"),
    before_id: int | None = Query(
        default=None, ge=1, description="取 id 小于它的那一段（往前翻页的游标），不传则取最新一页"
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # 归属校验走 get_owned（别人的会话一律 404）。消息**不再直接序列化 ORM 对象**，
    # 而是过一遍 load_messages_for_display：引用了"此刻已无权访问"的知识库的答案
    # 会被整条隐藏（撤销授权不能通过历史回放被绕开）。
    conv = conversation_service.get_owned(db, current_user, conversation_id)
    messages, has_more = conversation_service.load_messages_for_display(
        db, current_user, conv, limit=limit, before_id=before_id
    )
    detail = ConversationDetail(
        id=conv.id,
        title=conv.title,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        has_more=has_more,
        messages=[MessageOut.model_validate(m) for m in messages],
    )
    return success(detail)


@router.delete("/{conversation_id}", summary="删除会话（连带它的全部消息）")
def delete_conversation(
    conversation_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    conversation_service.delete_conversation(db, current_user, conversation_id)
    return success(None, "已删除")
