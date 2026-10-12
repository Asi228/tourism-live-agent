"""管理操作审计。

调用方只表达"发生了什么"（业务语义），动作名与 detail 结构由这里统一拼 ——
否则早晚会出现两个地方对同一个动作写出不同的 action 字符串，
而审计的价值全在"能被可靠地检索出来"。

事务边界仍在 service 层：这里只 `flush`，由**改动它的那个业务 service** 一起 commit，
保证"改动成功 ⇔ 有记录"。
"""

import logging

from sqlalchemy.orm import Session

from app.models.audit_log import (
    ACTION_KB_CREATE,
    ACTION_KB_DELETE,
    ACTION_KB_VISIBILITY,
    ACTION_MEMBER_GRANT,
    ACTION_MEMBER_REVOKE,
    ACTION_ROLE_CHANGE,
    ALL_ACTIONS,
    AuditLog,
)
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.repositories import audit_log_repository

logger = logging.getLogger(__name__)

# 各种动作在界面上的中文说明（前端直接显示，不用自己维护一份映射）
ACTION_TEXT = {
    ACTION_ROLE_CHANGE: "变更角色",
    ACTION_MEMBER_GRANT: "授权知识库",
    ACTION_MEMBER_REVOKE: "撤销授权",
    ACTION_KB_CREATE: "创建知识库",
    ACTION_KB_DELETE: "删除知识库",
    ACTION_KB_VISIBILITY: "变更可见性",
}

# 动作清单对外暴露（`GET /api/audit/actions` 用它渲染前端筛选下拉）
__all__ = ["ACTION_TEXT", "ALL_ACTIONS"]


def record_role_change(
    db: Session, *, actor: User, target: User, from_code: str, to_code: str
) -> AuditLog:
    return audit_log_repository.create(
        db,
        actor_id=actor.id,
        actor_name=actor.username,
        action=ACTION_ROLE_CHANGE,
        target_type="user",
        target_id=target.id,
        target_name=target.username,
        detail={"from": from_code, "to": to_code},
    )


def record_member_grant(
    db: Session, *, actor: User, kb: KnowledgeBase, target_user: User,
    permission: str, previous: str | None,
) -> AuditLog:
    return audit_log_repository.create(
        db,
        actor_id=actor.id,
        actor_name=actor.username,
        action=ACTION_MEMBER_GRANT,
        target_type="knowledge_base",
        target_id=kb.id,
        target_name=kb.name,
        detail={
            "user_id": target_user.id,
            "username": target_user.username,
            "permission": permission,
            # 之前是什么权限也记下来 —— "从 read 提到 admin" 和"新授权"是两件事
            "previous": previous,
        },
    )


def record_member_revoke(
    db: Session, *, actor: User, kb: KnowledgeBase,
    target_user_id: int, target_name: str, previous: str,
) -> AuditLog:
    """撤销授权。

    这里收 `id + name` 而不是 `User` 对象：撤销时目标用户的 ORM 对象可能已经不在了
    （极端情况：用户刚被删、成员行还在），而**审计不该因为取不到名字而失败** ——
    拿不到名字就记 id，比丢掉这条记录强。
    """
    return audit_log_repository.create(
        db,
        actor_id=actor.id,
        actor_name=actor.username,
        action=ACTION_MEMBER_REVOKE,
        target_type="knowledge_base",
        target_id=kb.id,
        target_name=kb.name,
        detail={
            "user_id": target_user_id,
            "username": target_name,
            "previous": previous,
        },
    )


def record_kb_create(db: Session, *, actor: User, kb: KnowledgeBase) -> AuditLog:
    return audit_log_repository.create(
        db,
        actor_id=actor.id,
        actor_name=actor.username,
        action=ACTION_KB_CREATE,
        target_type="knowledge_base",
        target_id=kb.id,
        target_name=kb.name,
        detail={"is_public": kb.is_public},
    )


def record_kb_delete(db: Session, *, actor: User, kb_id: int, kb_name: str) -> AuditLog:
    """删库要先记再删：库删掉之后就没有对象可供引用 id/name 了，只能靠快照。"""
    return audit_log_repository.create(
        db,
        actor_id=actor.id,
        actor_name=actor.username,
        action=ACTION_KB_DELETE,
        target_type="knowledge_base",
        target_id=kb_id,
        target_name=kb_name,
    )


def record_kb_visibility(
    db: Session, *, actor: User, kb: KnowledgeBase, from_public: bool, to_public: bool
) -> AuditLog:
    """知识库改成公开 / 私有。

    ⚠️ **这是权限开关，不是元数据** —— `is_public=True` 之后所有登录用户至少
    有 read，而且**下一次请求就生效**（`get_accessible_kb_ids` 立刻把它算进检索范围），
    比改角色生效还快（改角色要等下次登录）。所以它必须留痕。

    存 `public/private` 而不是 `true/false`：几个月后翻审计的人不该需要
    对照代码猜"false 是不是代表私有了"。
    """
    return audit_log_repository.create(
        db,
        actor_id=actor.id,
        actor_name=actor.username,
        action=ACTION_KB_VISIBILITY,
        target_type="knowledge_base",
        target_id=kb.id,
        target_name=kb.name,
        detail={
            "from": "public" if from_public else "private",
            "to": "public" if to_public else "private",
        },
    )


def list_recent(
    db: Session, *, limit: int = 20, action: str | None = None,
    actor_id: int | None = None, before_id: int | None = None,
) -> list[dict]:
    """最近的管理动作，转成纯 dict 交给上层（附一句中文说明）。

    `limit` 由调用方多要一条来判 `has_more`（keyset 的常规做法）。
    """
    rows = audit_log_repository.list_recent(
        db, limit=limit, action=action, actor_id=actor_id, before_id=before_id
    )
    return [
        {
            "id": r.id,
            "actor_id": r.actor_id,
            "actor_name": r.actor_name,
            "action": r.action,
            "action_text": ACTION_TEXT.get(r.action, r.action),
            "target_type": r.target_type,
            "target_id": r.target_id,
            "target_name": r.target_name,
            "detail": r.detail,
            "created_at": r.created_at,
        }
        for r in rows
    ]
