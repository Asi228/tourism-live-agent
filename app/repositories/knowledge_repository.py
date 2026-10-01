"""知识库数据访问。只负责查/写数据库，不做权限判断。"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.knowledge_base import KnowledgeBase


def get_by_id(db: Session, kb_id: int) -> KnowledgeBase | None:
    return db.get(KnowledgeBase, kb_id)


def get_by_name(db: Session, name: str) -> KnowledgeBase | None:
    return db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == name))


def list_all(db: Session) -> list[KnowledgeBase]:
    return list(db.scalars(select(KnowledgeBase).order_by(KnowledgeBase.id)))


def list_by_ids(db: Session, kb_ids: list[int]) -> list[KnowledgeBase]:
    """按 id 列表查（列表为空时直接返回空，避免生成 IN () 这种非法 SQL）。"""
    if not kb_ids:
        return []
    stmt = (
        select(KnowledgeBase)
        .where(KnowledgeBase.id.in_(kb_ids))
        .order_by(KnowledgeBase.id)
    )
    return list(db.scalars(stmt))


def list_public(db: Session) -> list[KnowledgeBase]:
    return list(db.scalars(select(KnowledgeBase).where(KnowledgeBase.is_public.is_(True))))


def list_by_owner(db: Session, owner_id: int) -> list[KnowledgeBase]:
    return list(db.scalars(select(KnowledgeBase).where(KnowledgeBase.owner_id == owner_id)))


def create(
    db: Session,
    *,
    name: str,
    description: str | None,
    owner_id: int,
    is_public: bool,
) -> KnowledgeBase:
    """只 flush 不 commit —— 事务边界归 Service 层。"""
    kb = KnowledgeBase(
        name=name,
        description=description,
        owner_id=owner_id,
        is_public=is_public,
    )
    db.add(kb)
    db.flush()
    return kb


def update(db: Session, kb: KnowledgeBase, fields: dict) -> KnowledgeBase:
    """按传入的字段增量更新。"""
    for key, value in fields.items():
        setattr(kb, key, value)
    db.flush()
    return kb


def delete(db: Session, kb: KnowledgeBase) -> None:
    """删除知识库，关联的成员记录由 ORM cascade 一起删掉。"""
    db.delete(kb)
    db.flush()
