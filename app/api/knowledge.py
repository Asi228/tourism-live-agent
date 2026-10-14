"""知识库接口。

权限矩阵：
  GET    /api/knowledge                     登录即可（只返回自己可见的）
  POST   /api/knowledge                     系统 admin（require_admin）
  GET    /api/knowledge/{id}                对该库 read
  PUT    /api/knowledge/{id}                对该库 admin（库级管理员）
  DELETE /api/knowledge/{id}                系统 admin（require_admin）
  GET    /api/knowledge/{id}/members        对该库 admin
  POST   /api/knowledge/{id}/members        对该库 admin
  DELETE /api/knowledge/{id}/members/{uid}  对该库 admin
  POST   /api/knowledge/{id}/vectorize      对该库 write（批量向量化该库所有已完成的文档）

设计取舍：创建 / 删除属于系统级生命周期操作，走 require_admin；
库内的修改与成员管理下放给库级 admin（owner 天然是库级 admin）。
"""

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, rate_limit, require_admin
from app.core.response import ApiResponse, success
from app.db.session import get_db
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    KnowledgeBaseUpdate,
    MemberAdd,
)
from app.schemas.search import VectorizeResult
from app.services import knowledge_service, vector_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/knowledge", tags=["知识库"])


def _kb_out(kb: KnowledgeBase, perm: str | None) -> dict:
    """拼知识库的响应体。

    my_permission 由调用方算好传进来：列表已经一次性算完，
    在这里再查一次就是每个库多一条 SQL。
    """
    data = KnowledgeBaseOut.model_validate(kb).model_dump(mode="json")
    data["my_permission"] = perm
    return data


@router.get("", summary="知识库列表（只返回我有权访问的）")
def list_knowledge(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = knowledge_service.list_my_kbs(db, current_user)
    return success([_kb_out(kb, perm) for kb, perm in items])


@router.post("", summary="创建知识库（仅系统管理员）")
def create_knowledge(
    payload: KnowledgeBaseCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    kb = knowledge_service.create_kb(db, payload, admin)
    return success(_kb_out(kb, knowledge_service.get_my_permission(db, admin, kb)), "创建成功")


@router.get("/{kb_id}", summary="知识库详情")
def get_knowledge(
    kb_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    kb = knowledge_service.ensure_kb_permission(db, current_user, kb_id, "read")
    return success(_kb_out(kb, knowledge_service.get_my_permission(db, current_user, kb)))


@router.put("/{kb_id}", summary="修改知识库")
def update_knowledge(
    kb_id: int,
    payload: KnowledgeBaseUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    kb = knowledge_service.update_kb(db, current_user, kb_id, payload)
    return success(_kb_out(kb, knowledge_service.get_my_permission(db, current_user, kb)), "修改成功")


@router.delete("/{kb_id}", summary="删除知识库（仅系统管理员）")
def delete_knowledge(
    kb_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    knowledge_service.delete_kb(db, kb_id, admin)
    return success(None, "删除成功")


@router.get("/{kb_id}/members", summary="成员列表")
def list_members(
    kb_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    members = knowledge_service.list_members(db, current_user, kb_id)
    return success([m.model_dump(mode="json") for m in members])


@router.post("/{kb_id}/members", summary="添加成员 / 修改成员权限")
def grant_member(
    kb_id: int,
    payload: MemberAdd,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    member = knowledge_service.grant_member(db, current_user, kb_id, payload)
    return success(member.model_dump(mode="json"), "授权成功")


@router.delete("/{kb_id}/members/{target_user_id}", summary="移除成员")
def revoke_member(
    kb_id: int,
    target_user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    knowledge_service.revoke_member(db, current_user, kb_id, target_user_id)
    return success(None, "已移除")


@router.post(
    "/{kb_id}/vectorize",
    response_model=ApiResponse[VectorizeResult],
    summary="批量向量化该知识库所有已完成的文档",
)
def vectorize_knowledge(
    kb_id: int,
    _: None = Depends(rate_limit("vectorize")),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = vector_service.vectorize_kb(db, current_user, kb_id)
    # 写成功但没落盘 ≠ 没事：重启后那批向量对检索就是不可见的，必须当场说出来
    tail = "" if data["flushed"] else "（未落盘，重启后可能检索不到，请到「知识库」的向量库自检查看）"
    return success(data, f"向量化完成，写入 {data['vectorized']} 条{tail}")
