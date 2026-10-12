"""管理操作审计的响应模型。

`action_text` 由后端下发（见 `audit_service.ACTION_TEXT`）—— 前端不维护第二份
动作→文案的映射，否则加一个动作必然漏改一处，界面上就会露出 `kb.member_grant`
这种原始字符串。
"""

from datetime import datetime

from pydantic import BaseModel, Field


class AuditItemOut(BaseModel):
    id: int
    actor_id: int = Field(description="操作者 id（**无外键**：删用户不该带走审计）")
    actor_name: str = Field(description="操作者用户名快照，改名/删号后仍读得懂")
    action: str
    action_text: str
    target_type: str
    target_id: int
    target_name: str | None = None
    detail: dict | None = None
    created_at: datetime


class AuditPageOut(BaseModel):
    items: list[AuditItemOut]
    has_more: bool = Field(description="由「多查一条」得出，不是再发一次 COUNT")


class AuditActionOut(BaseModel):
    action: str
    text: str
