"""长期记忆的编排（Phase 11）。

四件事：列出 / 写入 / 删除 / 给提示词取用。事务边界在 service 层（项目硬约定）：
repository 只 flush，commit 由这里决定。

⚠️ **归属校验一律 404**（与会话一致）：403 等于确认"这条 id 存在，只是不归你"，
白送一条可枚举的信息。唯一入口是 `user_memory_repository.get_owned`，
它把 `user_id` 直接写进 WHERE，而不是"先查出来再比"。

⚠️ 两个上限**不是一回事**，别混（这与 `CONVERSATION_MAX_HISTORY_TURNS` /
`CONVERSATION_MAX_MESSAGES` 的区分同源）：
  · `USER_MEMORY_MAX_PER_USER` 管**库里存多少** → 超了拒绝写入，**不可逆**；
  · `USER_MEMORY_MAX_ITEMS` / `USER_MEMORY_MAX_CHARS` 管**送进提示词多少** →
    只影响这一问的上下文，数据仍在库里，**可逆**。
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.user import User
from app.models.user_memory import CONTENT_MAX_LENGTH, UserMemory
from app.rag import prompt
from app.repositories import user_memory_repository
from app.services import conversation_service

logger = logging.getLogger(__name__)


def list_memories(db: Session, user: User) -> list[UserMemory]:
    """当前用户的全部记忆，最近更新的在前。"""
    return user_memory_repository.list_by_user(db, user.id)


def create_memory(
    db: Session,
    user: User,
    content: str,
    conversation_id: int | None = None,
) -> UserMemory:
    """写入一条记忆（显式）。

    `conversation_id` 是可选的追溯线索；传了就先校验归属 ——
    拿到别人的会话 id 应当立刻 404，不该先把它写进自己的记忆里。

    ⚠️ **`USER_MEMORY_ENABLED` 只控制"要不要拼进提示词"，不拦写入。**
    这是刻意的：让用户可以平时慢慢积累，需要时再打开开关用。
    别"顺手"在这里加个 gate —— 那会悄悄改掉这个语义（有用例盯着，见
    `test_disabled_switch_still_allows_writing`）。
    """
    text = (content or "").strip()
    if not text:
        raise AppException(ErrorCode.PARAM_ERROR, "记忆内容不能为空")
    if len(text) > CONTENT_MAX_LENGTH:
        raise AppException(
            ErrorCode.PARAM_ERROR, f"一条记忆最多 {CONTENT_MAX_LENGTH} 字，请拆成多条"
        )

    if conversation_id is not None:
        conversation_service.get_owned(db, user, conversation_id)

    # 存量上限：超了**拒绝写入并提示**，不静默丢最旧的 ——
    # 静默删用户数据是数据损坏，让用户自己删只是麻烦（口径同 CONVERSATION_MAX_PER_USER）。
    cap = settings.USER_MEMORY_MAX_PER_USER
    if cap > 0 and user_memory_repository.count_by_user(db, user.id) >= cap:
        raise AppException(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"长期记忆已达上限（{cap} 条），请先删除一些再添加",
        )

    row = user_memory_repository.create(
        db, user.id, text, source_conversation_id=conversation_id
    )
    db.commit()
    logger.info("新增长期记忆 user=%s memory_id=%s", user.username, row.id)
    return row


def delete_memory(db: Session, user: User, memory_id: int) -> None:
    """删除一条记忆。**不可逆**。"""
    row = user_memory_repository.get_owned(db, memory_id, user.id)
    if row is None:
        raise AppException(ErrorCode.NOT_FOUND, "记忆不存在")
    user_memory_repository.delete(db, row)
    db.commit()
    logger.info("删除长期记忆 user=%s memory_id=%s", user.username, memory_id)


def section_for_prompt(db: Session, user: User) -> str:
    """给提示词用的记忆段 —— 开关关、条数为 0、或没有任何记忆时都返回空串。

    只在**真的要调模型之前**才调用它（纯 RAG 与 Agent 都在生成那一步），
    这样"没命中、不调模型"的路径不会白查一次库。
    """
    if not settings.USER_MEMORY_ENABLED:
        return ""
    limit = settings.USER_MEMORY_MAX_ITEMS
    if limit <= 0:
        return ""

    rows = user_memory_repository.list_by_user(db, user.id, limit=limit)
    section = prompt.build_memory_section(
        [m.content for m in rows], settings.USER_MEMORY_MAX_CHARS
    )
    if section:
        logger.info("长期记忆已拼入提示词 user=%s 条数=%s", user.username, len(rows))
    return section
