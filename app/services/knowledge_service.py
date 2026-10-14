"""知识库业务逻辑 + 权限判定。

权限来源（按优先级取第一个命中，最后再兜公开库）：
  ① 系统角色 = admin                 → 对所有知识库有 admin 权限
  ② kb.owner_id = 当前用户            → 对该库有 admin 权限
  ③ knowledge_base_members 记录       → 按记录的 permission（read / write / admin）
  ④ kb.is_public = True              → 所有登录用户至少 read

档位分工（Phase 4 起 write 已生效）：
  read  → 看知识库 / 文档 / chunk
  write → 文档读写：上传、删除文档（documents 接口用这两档）
  admin → 知识库元数据修改、成员管理
注意"改知识库本身"（PUT /api/knowledge/{id}）仍要求 admin，不对 write 开放 ——
write 是"往知识库里写内容"，不是"改这个库的配置"。

对外最重要的两个函数：
  get_accessible_kb_ids()  —— Phase 5 拿它去过滤 Milvus，实现"检索期权限过滤"
  ensure_kb_permission()   —— 接口层统一用它做权限校验
"""

import logging

from sqlalchemy.orm import Session

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.models.knowledge_base import KnowledgeBase
from app.models.knowledge_base_member import PERMISSION_LEVEL, KnowledgeBaseMember
from app.models.user import User
from app.repositories import (
    document_repository,
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
from app.services import audit_service
from app.utils import file_storage

logger = logging.getLogger(__name__)

PERM_READ = "read"
PERM_WRITE = "write"
PERM_ADMIN = "admin"

SYSTEM_ADMIN_ROLE = "admin"

# "不存在"和"无权访问"对外必须是同一句话，否则等于把枚举能力还回来了
NO_ACCESS_MSG = "知识库不存在或无权访问"

# `update_kb` 里要额外剔掉 null 的字段：这些列在库里是 NOT NULL，
# 显式传 null 进来被 setattr 上去，直到 commit 才炸（见 `update_kb` 的说明）。
# `description` 是 nullable，**刻意不在这个集合里** —— 传 null 表示清空它。
_NOT_NULL_UPDATE_FIELDS = frozenset({"name", "is_public"})


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
    # 审计与改动同事务（audit_service 只 flush，这里一起 commit）
    audit_service.record_kb_create(db, actor=owner, kb=kb)
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

    # exclude_unset：只更新客户端真正传上来的字段，没传的保持不变。
    #
    # ⚠️ **但它挡不住"显式传 null"** —— 传了就算 set。所以 `{"name": null}` 会原样
    # 留在 fields 里，被 setattr 到 NOT NULL 列上，一路走到 commit 才炸（59999）——
    # 从报错完全看不出是这里。所以对 NOT NULL 的字段再剔一次 None。
    # "没传"和"传 null"在更新接口里语义相同：都是**不改这一项**。
    #
    # ⚠️ `description` 是 nullable 的**例外**，不在剔除名单里：
    # 传 null 表示"清空描述"，那是合法意图，不能一起抹掉。
    fields = {
        k: v
        for k, v in payload.model_dump(exclude_unset=True).items()
        if not (v is None and k in _NOT_NULL_UPDATE_FIELDS)
    }
    if "name" in fields:
        # 必须比对 id：真库列排序规则是 utf8mb4_unicode_ci（大小写不敏感），
        # 把 "Tech" 改成 "tech" 会查到这条记录自己，不比对 id 就误报"名称已存在"
        same_name = knowledge_repository.get_by_name(db, fields["name"])
        if same_name is not None and same_name.id != kb.id:
            raise AppException(ErrorCode.PARAM_ERROR, "该知识库名称已存在")

    if fields:
        # ⚠️ `is_public` 是**权限开关**不是元数据：置 True 之后所有登录用户至少
        # 有 read，而且下一次请求就生效（`get_accessible_kb_ids` 立刻把它算进
        # 检索范围）—— 比改角色生效还快。所以它必须留痕。
        # 这个 from 值要在 update 之前取：update 之后 kb.is_public 已经被改掉了。
        was_public = kb.is_public
        knowledge_repository.update(db, kb, fields)
        if "is_public" in fields and fields["is_public"] != was_public:
            audit_service.record_kb_visibility(
                db, actor=user, kb=kb, from_public=was_public,
                to_public=bool(fields["is_public"]),
            )
    db.commit()
    logger.info("修改知识库 kb_id=%s fields=%s", kb.id, list(fields))
    return kb


def delete_kb(db: Session, kb_id: int, actor: User) -> None:
    """删除知识库：成员记录、文档行、文档的磁盘文件一起清。

    权限由接口层的 require_admin 保证 —— 删除被划为**系统管理员专属**操作：
    它和"创建知识库"是同一级别的生命周期动作，且破坏性最大，不适合下放给库级 admin。

    磁盘文件必须在 ORM 删掉 Document 行**之前**取路径：行一旦删了，
    file_path 就再也查不到，那些文件会在 uploads/ 里永久变成无主垃圾
    （对企业知识库来说，"已删除的库内容还在服务器上"是合规问题，不只是脏文件）。

    注意 Phase 5 之后这里还要多清一处：Milvus 里的向量。DB 级联删不到远端数据。
    """
    kb = knowledge_repository.get_by_id(db, kb_id)
    if kb is None:
        raise AppException(ErrorCode.NOT_FOUND, "知识库不存在")

    name = kb.name
    # 先取路径再删行：删完就查不到了
    doc_paths = document_repository.list_file_paths_by_kb(db, kb_id)
    # 审计要在删之前记：库删掉后就没有对象可供引用 id/name 了（记录里用的是快照）
    audit_service.record_kb_delete(db, actor=actor, kb_id=kb_id, kb_name=name)
    knowledge_repository.delete(db, kb)  # 成员、文档、chunk 由 cascade 一起删
    db.commit()

    deleted = file_storage.delete_files(doc_paths)
    file_storage.remove_dir_if_empty(settings.upload_path / str(kb_id))
    _purge_vectors(kb_id)
    logger.info(
        "删除知识库 kb_id=%s name=%s | 清理文档文件 %s/%s 个",
        kb_id, name, deleted, len(doc_paths),
    )


def _purge_vectors(kb_id: int) -> None:
    """尽力清掉 Milvus 里属于这个知识库的向量。

    不清的话，库删了而向量还在，检索就会命中"孤儿向量"（正文已不存在）。
    局部 import + 吃掉异常的理由同 document_service._purge_vectors：
    删知识库不该因为 Milvus 暂时连不上而失败。
    """
    try:
        from app.rag import vector_store

        removed = vector_store.delete_by_kb(kb_id)
        if removed:
            # 删除记录也要落盘，否则服务端重启后这次删除可能没生效、向量"复活"成孤儿
            logger.info("已清理知识库向量 kb_id=%s count=%s flushed=%s",
                        kb_id, removed, vector_store.flush())
    except Exception as e:  # noqa: BLE001
        logger.warning("清理知识库向量失败（不影响删除）kb_id=%s err=%s", kb_id, e)


# ==================== 成员管理 ====================


def _valid_permission(value: str) -> str | None:
    """合法档位才返回，否则返回 None。

    与 _member_perm_map 同一口径：库里存了非法档位（人工改库、脚本导入）
    就等同于"没有这条授权记录"，绝不把它当成合法权限往外吐。
    """
    return value if value in PERMISSION_LEVEL else None


def _member_out(member: KnowledgeBaseMember, permission: str) -> MemberOut:
    """构造成员响应模型。

    permission 必须由调用方保证合法：
      读路径先过 _valid_permission 过滤，写路径来自受 Literal 约束的请求体。
    注意 MemberOut.permission 故意声明成 str 而不是 Literal —— 否则库里一行脏数据
    会让 pydantic 构造失败并变成 500，而我们想要的是记日志 + 跳过。
    """
    return MemberOut(
        id=member.id,
        user_id=member.user_id,
        username=member.user.username,
        permission=permission,
        created_at=member.created_at,
    )


def list_members(db: Session, user: User, kb_id: int) -> list[MemberOut]:
    ensure_kb_permission(db, user, kb_id, PERM_ADMIN)

    result: list[MemberOut] = []
    for member in knowledge_member_repository.list_by_kb(db, kb_id):
        perm = _valid_permission(member.permission)
        if perm is None:
            logger.warning(
                "成员表存在非法权限值，已从成员列表忽略 member_id=%s kb_id=%s permission=%r",
                member.id, member.knowledge_base_id, member.permission,
            )
            continue
        result.append(_member_out(member, perm))
    return result


def grant_member(db: Session, user: User, kb_id: int, payload: MemberAdd) -> MemberOut:
    """添加成员；已存在则更新权限（幂等，不会产生重复行）。"""
    kb = ensure_kb_permission(db, user, kb_id, PERM_ADMIN)

    target = user_repository.get_by_id(db, payload.user_id)
    if target is None:
        raise AppException(ErrorCode.NOT_FOUND, "用户不存在")
    if target.id == kb.owner_id:
        raise AppException(ErrorCode.PARAM_ERROR, "创建者默认拥有全部权限，无需授权")

    member = knowledge_member_repository.get(db, kb_id, payload.user_id)
    previous = member.permission if member is not None else None
    if member is None:
        member = knowledge_member_repository.create(
            db, kb_id=kb_id, user_id=payload.user_id, permission=payload.permission
        )
    else:
        member = knowledge_member_repository.update_permission(
            db, member, payload.permission
        )
    audit_service.record_member_grant(
        db, actor=user, kb=kb, target_user=target,
        permission=payload.permission, previous=previous,
    )
    db.commit()
    logger.info(
        "授权知识库成员 kb_id=%s target=%s perm=%s by=%s",
        kb_id, target.username, payload.permission, user.username,
    )

    perm = _valid_permission(member.permission)
    if perm is None:
        # 正常走不到：payload.permission 已被 Literal 约束
        raise AppException(ErrorCode.DB_ERROR, "成员权限数据异常")
    return _member_out(member, perm)


def revoke_member(db: Session, user: User, kb_id: int, target_user_id: int) -> None:
    kb = ensure_kb_permission(db, user, kb_id, PERM_ADMIN)
    if target_user_id == kb.owner_id:
        raise AppException(ErrorCode.PARAM_ERROR, "不能移除知识库创建者")

    member = knowledge_member_repository.get(db, kb_id, target_user_id)
    if member is None:
        raise AppException(ErrorCode.NOT_FOUND, "该用户不是此知识库的成员")

    target = user_repository.get_by_id(db, target_user_id)
    audit_service.record_member_revoke(
        db, actor=user, kb=kb,
        target_user_id=target_user_id,
        target_name=target.username if target else f"user#{target_user_id}",
        previous=member.permission,
    )
    knowledge_member_repository.delete(db, member)
    db.commit()
    logger.info("移除知识库成员 kb_id=%s target_user_id=%s", kb_id, target_user_id)
