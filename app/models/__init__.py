"""模型导出。

集中在这里导入，好处有两个：
1. 别处只要 `from app.models import User` 就行
2. `init_db()` 里导入本包后，Base.metadata 才能拿到全部表定义
"""

from app.models.agent_log import AgentLog
from app.models.audit_log import (
    ACTION_KB_CREATE,
    ACTION_KB_DELETE,
    ACTION_MEMBER_GRANT,
    ACTION_MEMBER_REVOKE,
    ACTION_ROLE_CHANGE,
    ALL_ACTIONS,
    AuditLog,
)
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
from app.models.knowledge_base_faq import (
    ANSWER_MAX_LENGTH as FAQ_ANSWER_MAX_LENGTH,
    FAQ_MAX_PER_KB,
    QUESTION_MAX_LENGTH as FAQ_QUESTION_MAX_LENGTH,
    KnowledgeBaseFaq,
)
from app.models.knowledge_base_member import KnowledgeBaseMember
from app.models.live_commerce import (
    InspectionBatch,
    InspectionRuleConfig,
    InspectionResult,
    InventorySnapshot,
    LiveSession,
    LiveSessionItem,
    PreparationPackage,
    Product,
    ProductSku,
    Promotion,
    PromotionSku,
)
from app.models.message import ALL_ROLES, ROLE_ASSISTANT, ROLE_USER, Message
from app.models.message_feedback import (
    ALL_RATINGS,
    ALL_REASONS,
    COMMENT_MAX_LENGTH as FEEDBACK_COMMENT_MAX_LENGTH,
    RATING_DOWN,
    RATING_UP,
    MessageFeedback,
)
from app.models.role import Role
from app.models.rpa import MonitoringPlan, MockPlatformSku, RpaApproval, RpaTask, RpaTaskStep
from app.models.user import User
from app.models.user_memory import CONTENT_MAX_LENGTH as MEMORY_MAX_LENGTH, UserMemory

__all__ = [
    "ACTION_KB_CREATE",
    "ACTION_KB_DELETE",
    "ACTION_MEMBER_GRANT",
    "ACTION_MEMBER_REVOKE",
    "ACTION_ROLE_CHANGE",
    "ALL_ACTIONS",
    "ALL_RATINGS",
    "ALL_REASONS",
    "ALL_ROLES",
    "ALL_STATUSES",
    "FAQ_ANSWER_MAX_LENGTH",
    "FAQ_MAX_PER_KB",
    "FAQ_QUESTION_MAX_LENGTH",
    "FEEDBACK_COMMENT_MAX_LENGTH",
    "MEMORY_MAX_LENGTH",
    "RATING_DOWN",
    "RATING_UP",
    "ROLE_ASSISTANT",
    "ROLE_USER",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_PENDING",
    "STATUS_PROCESSING",
    "TITLE_MAX_LENGTH",
    "AgentLog",
    "AuditLog",
    "Conversation",
    "Document",
    "DocumentChunk",
    "KnowledgeBase",
    "KnowledgeBaseFaq",
    "KnowledgeBaseMember",
    "InventorySnapshot",
    "InspectionBatch",
    "InspectionRuleConfig",
    "InspectionResult",
    "LiveSession",
    "LiveSessionItem",
    "Message",
    "MessageFeedback",
    "PreparationPackage",
    "Product",
    "ProductSku",
    "Promotion",
    "PromotionSku",
    "Role",
    "MockPlatformSku",
    "MonitoringPlan",
    "RpaApproval",
    "RpaTask",
    "RpaTaskStep",
    "User",
    "UserMemory",
]
