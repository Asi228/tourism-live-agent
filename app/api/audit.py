"""管理操作审计接口（仅系统管理员）。

回答的是"**谁在什么时候把谁的权限改成了什么**" —— 改角色、授权、删库这些
能改变"谁能看到什么"的动作都留痕在这里。详情与取舍见 `app/services/audit_service.py`。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.audit import AuditActionOut, AuditPageOut
from app.services import audit_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audit", tags=["审计"])


@router.get(
    "",
    summary="管理操作审计（仅系统管理员）",
    response_model=ApiResponse[AuditPageOut],
)
def list_audit_logs(
    limit: int = Query(default=20, ge=1, le=200),
    action: str | None = Query(default=None, max_length=64, description="按动作名精确筛选，如 kb.member_grant"),
    actor_id: int | None = Query(default=None, ge=1, description="只看某个人做的操作"),
    before_id: int | None = Query(default=None, ge=1, description="往前翻：传当前最旧那条的 id"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """最近的管理动作，新的在前。每条带 `action_text`（中文说明）与 `detail`。

    **翻页用 keyset（`before_id`）不用 offset** —— 审计是**只追加**的，新记录不断
    从头部进来，用 offset 翻页会在翻的过程中把已看过的行往下推，导致漏条 / 重条。
    判 `has_more` 的做法是**多查一条**。

    `action` / `actor_id` 两个筛选对应表上的两个索引 —— 建了索引就得有查询用得上，
    否则"最近 20 条"看多了就成了一张没人翻的流水。
    """
    # 多要一条：拿到了说明还有下一页
    rows = audit_service.list_recent(
        db, limit=limit + 1, action=action, actor_id=actor_id, before_id=before_id
    )
    has_more = len(rows) > limit
    items = rows[:limit]
    return success({"items": items, "has_more": has_more})


@router.get(
    "/actions",
    summary="审计动作清单（仅系统管理员）",
    response_model=ApiResponse[list[AuditActionOut]],
)
def list_actions(admin: User = Depends(require_admin)):
    """可选的动作名 + 中文说明。

    前端用它渲染筛选下拉 —— 让前端自己硬编码一份动作列表，就等于在
    后端之外维护第二份映射，加一个动作时必然漏改。
    """
    return success([
        {"action": a, "text": audit_service.ACTION_TEXT.get(a, a)}
        for a in audit_service.ALL_ACTIONS
    ])
