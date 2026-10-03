"""向量库运维接口（自检 + 孤儿向量清理）。

为什么需要它：
  `vector_store.health() / count() / list_kb_ids()` 这几块能力之前只被我的验证脚本用过，
  脚本一删就成了零引用的死代码。而它们恰好就是"孤儿向量"这条链路缺少的三块拼图 ——
  **孤儿向量在界面上完全不可见**（检索时被静默丢弃），需要一个能看见它的入口。

权限：全部要求**系统管理员**。自检结果会暴露 collection 名和知识库 id 清单，
不是普通员工该看的东西。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.response import success
from app.db.session import get_db
from app.models.user import User
from app.services import vector_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vector", tags=["向量库运维"])


@router.get("/health", summary="向量库自检（仅系统管理员）")
def vector_health(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return success(vector_service.self_check(db))


@router.post("/purge-orphans", summary="清理孤儿向量（仅系统管理员）")
def purge_orphans(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    data = vector_service.purge_orphans(db)
    return success(data, f"已清理 {data['removed']} 条孤儿向量")
