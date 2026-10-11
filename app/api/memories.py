"""长期记忆接口（Phase 11）。

  GET    /api/memories          我的长期记忆（最近更新的在前）
  POST   /api/memories          写入一条
  DELETE /api/memories/{id}     删除一条

**只支持显式写入**：由用户（或前端界面）明确说"记住这件事"。
刻意不做"每轮问答后自动提炼" —— 那要多花一次模型调用，而且会把
"刚才在聊什么"这类一次性内容也塞进长期记忆，越攒越脏；
要做得先解决"什么才算值得长期记住"，那是另一件事。

归属校验在 service 层：拿到别人的 id 一律 404「记忆不存在」，不是 403 ——
口径与会话接口一致（403 等于确认了这个 id 存在）。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import success
from app.db.session import get_db
from app.models.user import User
from app.schemas.memory import MemoryCreate, MemoryOut
from app.services import memory_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories", tags=["长期记忆"])


@router.get("", summary="我的长期记忆（最近更新的在前）")
def list_memories(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = memory_service.list_memories(db, current_user)
    return success([MemoryOut.model_validate(r).model_dump(mode="json") for r in rows])


@router.post("", summary="写入一条长期记忆")
def create_memory(
    payload: MemoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = memory_service.create_memory(
        db, current_user, payload.content, payload.conversation_id
    )
    return success(MemoryOut.model_validate(row).model_dump(mode="json"), "已记住")


@router.delete("/{memory_id}", summary="删除一条长期记忆")
def delete_memory(
    memory_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    memory_service.delete_memory(db, current_user, memory_id)
    return success(None, "已删除")
