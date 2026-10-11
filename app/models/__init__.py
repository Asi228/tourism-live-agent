"""模型导出。

集中在这里导入，好处有两个：
1. 别处只要 `from app.models import User` 就行
2. `init_db()` 里导入本包后，Base.metadata 才能拿到全部表定义
"""

from app.models.agent_log import AgentLog
from app.models.conversation import TITLE_MAX_LENGTH, Conversation
from app.models.document import (
    ALL_STATUSES,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_PROCESSING,
    Document,
)
from app.models.document_chunk import DocumentChunk
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_base_member import KnowledgeBaseMember
from app.models.message import ALL_ROLES, ROLE_ASSISTANT, ROLE_USER, Message
from app.models.role import Role
from app.models.user import User
from app.models.user_memory import CONTENT_MAX_LENGTH as MEMORY_MAX_LENGTH, UserMemory

__all__ = [
    "ALL_ROLES",
    "ALL_STATUSES",
    "MEMORY_MAX_LENGTH",
    "ROLE_ASSISTANT",
    "ROLE_USER",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_PROCESSING",
    "TITLE_MAX_LENGTH",
    "AgentLog",
    "Conversation",
    "Document",
    "DocumentChunk",
    "KnowledgeBase",
    "KnowledgeBaseMember",
    "Message",
    "Role",
    "User",
    "UserMemory",
]
