"""概览统计接口。

两个端点，服务对象都是"概览"页，但**分开是有意的**：

  · `/overview` —— 首屏要显示的数字。一个请求拿全，不掺图表数据；
  · `/trends`   —— 图表用的趋势（近 N 天提问、各库文档数）。按需再取。

不分家的理由：`question_trend` 里补了一堆 count=0 的点（没有提问的日子也得有，
否则折线断格），对首屏是纯噪音；而图表只在用户真去看的时候才需要。

都不做筛选参数（除 `trends` 的天数）—— 概览页要的就是"当前这个人当前能看到的全貌"，
加筛选反而没人用。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.user import User
from app.schemas.stats import OverviewOut, TrendsOut
from app.services import stats_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/stats", tags=["统计"])


@router.get(
    "/overview",
    summary="概览统计（按当前用户可见范围）",
    response_model=ApiResponse[OverviewOut],
)
def overview(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """数字口径都在 `stats_service.overview` 的注释里 —— 核心是**按可见范围**算，
    不是全库。员工有权看到自己的概览，但看不到别人的数据。"""
    return success(stats_service.overview(db, current_user))


@router.get(
    "/trends",
    summary="趋势数据（概览页图表：近 N 天提问数、各库文档数）",
    response_model=ApiResponse[TrendsOut],
)
def trends(
    days: int = Query(7, ge=1, le=90, description="提问趋势覆盖的天数"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """口径与 `/overview` 完全一致（同一个 `get_accessible_kb_ids`）——
    否则会出现"概览说 3 份文档、图表说 5 份"这种自相矛盾。

    `days` 上限 90：再长对"概览页一条折线"没有意义，只会把一次请求的数据量撑大。
    """
    return success(stats_service.trends(db, current_user, days))
