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

响应结构用 `response_model=ApiResponse[...]` 显式声明（与会话接口同批补上，
它们曾是全项目最后两个 0 声明的模块）。`MemoryOut` 有 `from_attributes=True`，
所以校验 ORM 对象不需要先手工 dump。DELETE 刻意不声明 —— 没有可声明的 data。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.memory import MemoryCreate, MemoryOut
from app.services import memory_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memories", tags=["长期记忆"])


@router.get(
    "",
    response_model=ApiResponse[list[MemoryOut]],
    summary="我的长期记忆（最近更新的在前）",
)
def list_memories(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    rows = memory_service.list_memories(db, current_user)
    # 同 conversations：声明了 response_model 就不再手工 model_dump，
    # 序列化交给 FastAPI，避免"校验一遍、丢字段"发生在没人看见的地方。
    return success([MemoryOut.model_validate(r) for r in rows])


@router.post("", response_model=ApiResponse[MemoryOut], summary="写入一条长期记忆")
def create_memory(
    payload: MemoryCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = memory_service.create_memory(
        db, current_user, payload.content, payload.conversation_id
    )
    return success(MemoryOut.model_validate(row), "已记住")


@router.delete("/{memory_id}", summary="删除一条长期记忆")
def delete_memory(
    memory_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    memory_service.delete_memory(db, current_user, memory_id)
    return success(None, "已删除")
