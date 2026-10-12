"""概览统计。

给"概览"页供数。几条设计取舍：

1. **口径按"我可见的范围"算**，不是全库 —— 员工看到"文档 3 份"应该就是他有权访问的
   那 3 份，而不是全公司的 300 份。管理员不受限（`get_accessible_kb_ids` 返回 None）。
2. **问答数只算自己的**：别人的提问量对他没有意义，也不该被看到。
3. **向量条数只给管理员**：它要打 Milvus 的 `count()`（一次远程调用），
   普通员工看概览不该触发这个开销；而且那是系统级指标。
"""

import logging

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories import stats_repository
from app.services import knowledge_service

logger = logging.getLogger(__name__)


def overview(db: Session, user: User) -> dict:
    """概览页需要的全部数字。

    ⚠️ `get_accessible_kb_ids` 返回 `None` 表示**不受限**（管理员），`[]` 表示
    "没有任何可见的库" —— 这两种必须分开传下去，统计层靠它区分"全量"与"空"。
    """
    allowed = knowledge_service.get_accessible_kb_ids(db, user)

    my_kbs = knowledge_service.list_my_kbs(db, user)
    doc_total, by_status = stats_repository.document_counts(db, allowed)
    q_total, q_today = stats_repository.question_counts(db, user.id)

    data = {
        "scope": {
            "kb_count": len(my_kbs),
            # 管理员是"不受限"而不是"有 N 个库"：这两个说法在界面上含义不同
            "unlimited": allowed is None,
        },
        "documents": {
            "total": doc_total,
            "by_status": by_status,
        },
        "questions": {"total": q_total, "today": q_today},
        "recent_documents": stats_repository.recent_documents(db, allowed, limit=5),
        "recent_questions": stats_repository.recent_questions(db, user.id, limit=5),
        # 非管理员这里是 None，不是 0 —— 前端据此**整块不渲染**，
        # 而不是显示一个"用户 0 人"的假数字
        "admin": _admin_section(db, user, allowed),
    }
    return data


def _admin_section(db: Session, user: User, allowed: list[int] | None) -> dict | None:
    """管理员才有的系统级指标。"""
    if user.role is None or user.role.code != "admin":
        return None
    vector_count = None
    try:
        from app.rag import vector_store

        vector_count = vector_store.count()
    except Exception as exc:  # noqa: BLE001
        # Milvus 挂了不该让整个概览页打不开：这一项给 null，前端显示"不可用"。
        # 概览是入口页，它的可用性优先于个别指标的完整性。
        logger.warning("概览取向量条数失败（不影响其他指标）：%s", str(exc)[:150])
    return {
        "user_count": stats_repository.count_users(db),
        "vector_count": vector_count,
        "unlimited_scope": allowed is None,
    }
