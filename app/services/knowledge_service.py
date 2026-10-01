"""知识库业务逻辑 + 权限判定。

权限来源（按优先级取第一个命中，最后再兜公开库）：
  ① 系统角色 = admin                 → 对所有知识库有 admin 权限
  ② kb.owner_id = 当前用户            → 对该库有 admin 权限
  ③ knowledge_base_members 记录       → 按记录的 permission（read / write / admin）
  ④ kb.is_public = True              → 所有登录用户至少 read

注意：write 档位当前没有任何接口使用（修改知识库要求 admin），
它是留给 Phase 4「文档写入」的档位，不是已经生效的写权限。

对外最重要的两个函数：
  get_accessible_kb_ids()  —— Phase 5 拿它去过滤 Milvus，实现"检索期权限过滤"
  ensure_kb_permission()   —— 接口层统一用它做权限校验
"""

import logging

from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_base_member import PERMISSION_LEVEL, KnowledgeBaseMember
from app.models.user import User
from app.repositories import (
    knowledge_member_repository,
    knowledge_repository,
    user_repository,
)
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseUpdate,
    MemberAdd,
    MemberOut,
)

logger = logging.getLogger(__name__)

PERM_READ = "read"
PERM_WRITE = "write"
PERM_ADMIN = "admin"

SYSTEM_ADMIN_ROLE = "admin"

# "不存在"和"无权访问"对外必须是同一句话，否则等于把枚举能力还回来了
NO_ACCESS_MSG = "知识库不存在或无权访问"


# ==================== 权限判定 ====================


def _member_perm_map(db: Session, user_id: int) -> dict[int, str]:
    """当前用户的成员授权记录：knowledge_base_id -> permission。

    库里存了非法档位（人工改库、脚本批量导入）的那条按"没有授权记录"处理：
    判权限不该因为一行脏数据就 KeyError 变成 500。
    """
    raw = knowledge_member_repository.map_kb_permissions_by_user(db, user_id)
    valid: dict[int, str] = {}
    for kb_id, permission in raw.items():
        if permission in PERMISSION_LEVEL:
            valid[kb_id] = permission
        else:
            logger.warning(
                "成员表存在非法权限值，已忽略 kb_id=%s user_id=%s permission=%r",
                kb_id, user_id, permission,
            )
    return valid


def _permission_of(
    db: Session, user: User, kb: KnowledgeBase, perm_map: dict[int, str] | None
) -> str | None:
    """权限判定的唯一实现。perm_map 为 None 时按需查一次（单库路径）。"""
    if user.role.code == SYSTEM_ADMIN_ROLE:
        return PERM_ADMIN
    if kb.owner_id == user.id:
        return PERM_ADMIN

    if perm_map is None:
        perm_map = _member_perm_map(db, user.id)
    member_perm = perm_map.get(kb.id)
    if member_perm is not None:
        return member_perm

    if kb.is_public:
        return PERM_READ
    return None


def get_my_permission(db: Session, user: User, kb: KnowledgeBase) -> str | None:
    """当前用户对**某一个**知识库的权限；完全没权限返回 None。

    批量场景（列表）请走 list_my_kbs，它一次把授权记录取完，不要在循环里调这个。
    """
    return _permission_of(db, user, kb, None)


def _accessible_ids(db: Session, user: User, perm_map: dict[int, str]) -> list[int]:
    """非系统管理员可访问的库 id：被授权的 + 公开库 + 自己创建的。"""
    ids: set[int] = set(perm_map)
    ids.update(kb.id for kb in knowledge_repository.list_public(db))
    ids.update(kb.id for kb in knowledge_repository.list_by_owner(db, user.id))
    return sorted(ids)


def get_accessible_kb_ids(db: Session, user: User) -> list[int] | None:
    """当前用户可访问的知识库 id 列表。

    **返回 None 表示不受限**（系统管理员），调用方需要单独处理这一种情况。

    这是"权限过滤必须发生在检索阶段"的落地点：
    Phase 5 的 Milvus 检索会拿这个列表拼 knowledge_base_id IN (...)。
    """
    if user.role.code == SYSTEM_ADMIN_ROLE:
        return None
    return _accessible_ids(db, user, _member_perm_map(db, user.id))


def ensure_kb_permission(
    db: Session, user: User, kb_id: int, need: str
) -> KnowledgeBase:
    """校验权限并返回知识库对象；不通过直接抛异常。

    need ∈ {read, write, admin}
    """
    if need not in PERMISSION_LEVEL:
        raise ValueError(f"非法的权限等级: {need}")

    kb = knowledge_repository.get_by_id(db, kb_id)
    if kb is None:
        # 与"无权访问"共用同一句 message 和同一个 code：
        # 只要两种情况的文案有差异，遍历 id 比对 message 就仍然能探测出系统里有哪些库
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)

    got = _permission_of(db, user, kb, None)
    if got is None:
        raise AppException(ErrorCode.NOT_FOUND, NO_ACCESS_MSG)

    if PERMISSION_LEVEL[got] < PERMISSION_LEVEL[need]:
        raise AppException(ErrorCode.FORBIDDEN, "对该知识库的权限不足")
    return kb


# ==================== 知识库 CRUD ====================


def create_kb(db: Session, payload: KnowledgeBaseCreate, owner: User) -> KnowledgeBase:
    if knowledge_repository.get_by_name(db, payload.name) is not None:
        raise AppException(ErrorCode.PARAM_ERROR, "该知识库名称已存在")

    kb = knowledge_repository.create(
        db,
        name=payload.name,
        description=payload.description,
        owner_id=owner.id,
        is_public=payload.is_public,
    )
    # 创建者写一条 admin 成员记录，这样"成员列表"里能看到自己，语义也完整
    knowledge_member_repository.create(
        db, kb_id=kb.id, user_id=owner.id, permission=PERM_ADMIN
    )
    db.commit()
    logger.info("创建知识库 kb_id=%s name=%s owner=%s", kb.id, kb.name, owner.username)
    return kb


def list_my_kbs(db: Session, user: User) -> list[tuple[KnowledgeBase, str | None]]:
    """列出当前用户可访问的知识库，附带自己对该库的权限。

    授权记录只查一次（map），列表里每项的权限直接从 map 算，
    不这么写的话每多一个库就多两条 SQL。
    """
    if user.role.code == SYSTEM_ADMIN_ROLE:
        return [(kb, PERM_ADMIN) for kb in knowledge_repository.list_all(db)]

    perm_map = _member_perm_map(db, user.id)
    items = knowledge_repository.list_by_ids(db, _accessible_ids(db, user, perm_map))
    return [(kb, _permission_of(db, user, kb, perm_map)) for kb in items]


def update_kb(
    db: Session, user: User, kb_id: int, payload: KnowledgeBaseUpdate
) -> KnowledgeBase:
    kb = ensure_kb_permission(db, user, kb_id, PERM_ADMIN)

    # exclude_unset：只更新客户端真正传上来的字段，没传的保持不变
    fields = payload.model_dump(exclude_unset=True)
    if "name" in fields:
        # 必须比对 id：真库列排序规则是 utf8mb4_unicode_ci（大小写不敏感），
        # 把 "Tech" 改成 "tech" 会查到这条记录自己，不比对 id 就误报"名称已存在"
        same_name = knowledge_repository.get_by_name(db, fields["name"])
        if same_name is not None and same_name.id != kb.id:
            raise AppException(ErrorCode.PARAM_ERROR, "该知识库名称已存在")

    if fields:
        knowledge_repository.update(db, kb, fields)
    db.commit()
    logger.info("修改知识库 kb_id=%s fields=%s", kb.id, list(fields))
    return kb


def delete_kb(db: Session, kb_id: int) -> None:
    """删除知识库。

    权限由接口层的 require_admin 保证 —— 删除被划为**系统管理员专属**操作：
    它和"创建知识库"是同一级别的生命周期动作，且会级联删掉成员、文档、向量，
    破坏性最大，不适合下放给库级 admin。
    """
    kb = knowledge_repository.get_by_id(db, kb_id)
    if kb is None:
        raise AppException(ErrorCode.NOT_FOUND, "知识库不存在")

    name = kb.name
    knowledge_repository.delete(db, kb)  # 成员记录由 cascade 一起删
    db.commit()
    logger.info("删除知识库 kb_id=%s name=%s", kb_id, name)


# ==================== 成员管理 ====================


def _member_out(member: KnowledgeBaseMember) -> MemberOut:
    return MemberOut(
        id=member.id,
        user_id=member.user_id,
        username=member.user.username,
        permission=member.permission,
        created_at=member.created_at,
    )


def list_members(db: Session, user: User, kb_id: int) -> list[MemberOut]:
    ensure_kb_permission(db, user, kb_id, PERM_ADMIN)
    return [_member_out(m) for m in knowledge_member_repository.list_by_kb(db, kb_id)]


def grant_member(db: Session, user: User, kb_id: int, payload: MemberAdd) -> MemberOut:
    """添加成员；已存在则更新权限（幂等，不会产生重复行）。"""
    kb = ensure_kb_permission(db, user, kb_id, PERM_ADMIN)

    target = user_repository.get_by_id(db, payload.user_id)
    if target is None:
        raise AppException(ErrorCode.NOT_FOUND, "用户不存在")
    if target.id == kb.owner_id:
        raise AppException(ErrorCode.PARAM_ERROR, "创建者默认拥有全部权限，无需授权")

    member = knowledge_member_repository.get(db, kb_id, payload.user_id)
    if member is None:
        member = knowledge_member_repository.create(
            db, kb_id=kb_id, user_id=payload.user_id, permission=payload.permission
        )
    else:
        member = knowledge_member_repository.update_permission(
            db, member, payload.permission
        )
    db.commit()
    logger.info(
        "授权知识库成员 kb_id=%s target=%s perm=%s by=%s",
        kb_id, target.username, payload.permission, user.username,
    )
    return _member_out(member)


def revoke_member(db: Session, user: User, kb_id: int, target_user_id: int) -> None:
    kb = ensure_kb_permission(db, user, kb_id, PERM_ADMIN)
    if target_user_id == kb.owner_id:
        raise AppException(ErrorCode.PARAM_ERROR, "不能移除知识库创建者")

    member = knowledge_member_repository.get(db, kb_id, target_user_id)
    if member is None:
        raise AppException(ErrorCode.NOT_FOUND, "该用户不是此知识库的成员")

    knowledge_member_repository.delete(db, member)
    db.commit()
    logger.info("移除知识库成员 kb_id=%s target_user_id=%s", kb_id, target_user_id)
