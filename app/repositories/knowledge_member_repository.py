"""知识库成员授权数据访问。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge_base_member import KnowledgeBaseMember


def get(db: Session, kb_id: int, user_id: int) -> KnowledgeBaseMember | None:
    """查某人在某库的授权记录。"""
    return db.scalar(
        select(KnowledgeBaseMember).where(
            KnowledgeBaseMember.knowledge_base_id == kb_id,
            KnowledgeBaseMember.user_id == user_id,
        )
    )


def list_by_kb(db: Session, kb_id: int) -> list[KnowledgeBaseMember]:
    stmt = (
        select(KnowledgeBaseMember)
        .where(KnowledgeBaseMember.knowledge_base_id == kb_id)
        .order_by(KnowledgeBaseMember.id)
    )
    return list(db.scalars(stmt))


def map_kb_permissions_by_user(db: Session, user_id: int) -> dict[int, str]:
    """某用户被直接授权的 knowledge_base_id -> permission 映射。

    一次查全，供列表批量判权限用（循环里逐个查就是 N+1）。
    """
    stmt = select(KnowledgeBaseMember.knowledge_base_id, KnowledgeBaseMember.permission).where(
        KnowledgeBaseMember.user_id == user_id
    )
    return {kb_id: permission for kb_id, permission in db.execute(stmt)}


def create(
    db: Session,
    *,
    kb_id: int,
    user_id: int,
    permission: str,
) -> KnowledgeBaseMember:
    member = KnowledgeBaseMember(
        knowledge_base_id=kb_id,
        user_id=user_id,
        permission=permission,
    )
    db.add(member)
    db.flush()
    return member


def update_permission(
    db: Session, member: KnowledgeBaseMember, permission: str
) -> KnowledgeBaseMember:
    member.permission = permission
    db.flush()
    return member


def delete(db: Session, member: KnowledgeBaseMember) -> None:
    db.delete(member)
    db.flush()
