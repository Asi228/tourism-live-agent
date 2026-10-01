"""模型导出。

集中在这里导入，好处有两个：
1. 别处只要 `from app.models import User` 就行
2. `init_db()` 里导入本包后，Base.metadata 才能拿到全部表定义
"""

from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_base_member import KnowledgeBaseMember
from app.models.role import Role
from app.models.user import User

__all__ = ["KnowledgeBase", "KnowledgeBaseMember", "Role", "User"]
