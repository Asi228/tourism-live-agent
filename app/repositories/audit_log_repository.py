"""管理操作审计的数据访问。

与其余 repository 一致的约定：
  · **只 flush、不 commit** —— 事务边界在 service 层。这条尤其重要：审计要和
    它记录的那次改动**同生共死**（改动成功才该有记录，失败就不该留）。
  · **不吞异常** —— 与 `agent_log_repository.create` 相反。那边是"审计写不进去也不能
    耽误用户拿到答案"，而这里恰恰相反：**审计失败必须让整个改动失败**。
    理由：改角色 / 授权是能改变"谁能看到什么"的操作，如果审计可以静默丢失，
    那就等于没有审计 —— 事后追责时你会发现关键的几次改动没有记录，
    而"有没有记录"恰恰是事后唯一的抓手。
"""

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)


def create(
    db: Session,
    *,
    actor_id: int,
    actor_name: str,
    action: str,
    target_type: str,
    target_id: int,
    target_name: str | None = None,
    detail: dict | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_id=actor_id,
        actor_name=actor_name,
        action=action,
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        detail=detail,
    )
    db.add(row)
    db.flush()
    return row


def list_recent(
    db: Session, *, limit: int = 20, action: str | None = None,
    actor_id: int | None = None, before_id: int | None = None,
) -> list[AuditLog]:
    """最近的管理动作，新的在前。

    排序次键用 `id DESC` 而不是只靠 `created_at`：DATETIME 只精确到秒，
    同一秒内发生两次改动时，只按时间排会得到不稳定的顺序（翻页会漏/重）。

    **翻页用 `id < before_id`（keyset）而不是 offset** —— 与"会话详情往前翻"
    同一套理由：审计是**只追加**的，新记录不断从头部进来，用 offset 翻页会
    在翻的过程中把已看过的行往下推 → 漏条、重条。

    三个筛选参数对应表上的三个索引（`action` / `actor_id` / `created_at`）——
    建了索引就要有查询用得上，否则"最近 20 条"看多了就成了一张没人翻的流水。
    """
    stmt = select(AuditLog).order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if actor_id is not None:
        stmt = stmt.where(AuditLog.actor_id == actor_id)
    if before_id is not None:
        stmt = stmt.where(AuditLog.id < before_id)
    return list(db.execute(stmt.limit(limit)).scalars().all())
