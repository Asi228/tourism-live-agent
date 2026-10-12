"""概览统计接口。

一个端点供"概览"页用：把散落在各处的数字收拢成一次请求。
不做参数 —— 概览页要的就是"当前这个人当前能看到的全貌"，加筛选反而没人用。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import success
from app.db.session import get_db
from app.models.user import User
from app.services import stats_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stats", tags=["统计"])


@router.get("/overview", summary="概览统计（按当前用户可见范围）")
def overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """数字口径都在 `stats_service.overview` 的注释里 —— 核心是**按可见范围**算，
    不是全库。员工有权看到自己的概览，但看不到别人的数据。"""
    return success(stats_service.overview(db, current_user))
