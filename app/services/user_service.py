"""用户管理业务逻辑。

注册 / 登录在 `auth_service`，这里管的是**管理员对用户的运维动作**：
查列表、改角色、看某人被授权了哪些库。

⚠️ 角色变更的三条硬约束都在 `change_role` 里 —— 它们不是"顺手加的校验"，
而是"这个接口能不能开"的前提，理由见函数注释。
"""

import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import AppException
from app.core.response import ErrorCode
from app.core.security import hash_password
from app.models.knowledge_base_member import KnowledgeBaseMember
from app.models.role import Role
from app.models.user import User
from app.repositories import knowledge_repository, role_repository, user_repository
from app.services import audit_service

logger = logging.getLogger(__name__)

# 拥有这个角色的人**必须在系统里始终至少剩一个**，否则没人能再改任何人的角色。
PRIVILEGED_ROLE = "admin"


def list_users(db: Session, keyword: str | None = None, limit: int = 50) -> list[User]:
    """按关键字定位用户。返回 ORM 对象，对外暴露哪些字段由接口的 Schema 决定。"""
    return user_repository.search(db, keyword, limit)


def count_users(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(User)) or 0


def count_admins(db: Session, *, for_update: bool = False) -> int:
    """当前有多少个 admin。

    ⚠️ **`change_role` 里必须用 `for_update=True`**，否则有 TOCTOU：
      · A、B 两个请求（比如两个管理员同时降级对方）各自 `SELECT COUNT(*)` 都看到 2；
      · 各自都通过"不能归零"这道闸门；
      · 两个 UPDATE 打的是**不同的行**，行锁不冲突 → 两条都提交 → **admin 归零**。
    归零之后没有任何人能再改角色，只能进数据库改 —— 而"除了直接改数据库"
    这句话在 docstring 里是当玩笑写的，这里不能让它成真。

    `with_for_update()` 让这条 SELECT 走**当前读**并锁住扫到的行，两个并发请求
    因此串行化：后到的那个重新数时已经是 1，会被正常拒掉。

    ⚠️ 注意"更新后再复查一次"**不是**等效修法：MySQL 默认隔离级别是
    REPEATABLE READ，事务内的一致性读用的是开始时的快照，复查仍会看到 2。
    要复查也只有加锁读才有效 —— 所以直接用这一条更省事。

    SQLite（测试环境）不支持 `SELECT ... FOR UPDATE`，SQLAlchemy 会忽略它，
    因此这里不需要按方言分支。
    """
    stmt = (
        select(func.count())
        .select_from(User)
        .join(Role, User.role_id == Role.id)
        .where(Role.code == PRIVILEGED_ROLE)
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalar(stmt) or 0


def change_role(db: Session, *, actor: User, target_id: int, role_code: str) -> dict:
    """把某个用户改成另一个系统角色。

    这是**提权入口**，三条约束缺一不可（都能在答辩时说清为什么要）：

    1. **不能改自己** —— 防手滑自锁。管理员把自己降成员工之后，如果他是唯一的
       admin，就再也没人能改回来了（除了直接改数据库）。
    2. **不能让 admin 归零** —— 与第 1 条互补：第 1 条挡的是"改自己"，
       这条挡的是"改别人之后系统里没有 admin 了"（比如两个 admin 互相降级对方）。
    3. **写审计** —— 改角色会改变"谁能看到什么"，必须留痕（谁、何时、从什么到什么）。
       `audit_service` 只 flush 不 commit，所以**改动与记录同生共死**：
       记录写不进去，这次改动也不会提交。

    `role_code` 不存在时返回 40000 而不是 40400：这是"入参不合法"，
    不是"你要找的东西不存在"。错误码选错会让前端提示成"用户不存在"，归因跑偏。
    """
    role = role_repository.get_by_code(db, role_code)
    if role is None:
        raise AppException(ErrorCode.PARAM_ERROR, f"角色不存在：{role_code}")

    target = user_repository.get_by_id(db, target_id)
    if target is None:
        raise AppException(ErrorCode.NOT_FOUND, "用户不存在")

    if target.id == actor.id:
        raise AppException(
            ErrorCode.PARAM_ERROR, "不能修改自己的角色（防止把自己锁在系统外）"
        )

    from_code = target.role.code if target.role else None
    if from_code == role_code:
        # 幂等：重复提交同一个角色直接当成功返回，不当错误 ——
        # 前端下拉可能被点两次，报错反而让人以为操作失败了。
        return {
            "id": target.id, "username": target.username,
            "role_code": role_code, "changed": False,
        }

    # for_update：这条闸门防的正是并发（两个管理员同时降级对方），而"先数后改"
    # 不加锁等于没防。理由见 count_admins 的 docstring。
    if from_code == PRIVILEGED_ROLE and count_admins(db, for_update=True) <= 1:
        raise AppException(
            ErrorCode.PARAM_ERROR,
            "系统里至少要保留一个管理员（这位是最后一个，不能再降级）",
        )

    target.role_id = role.id
    target.role = role
    db.flush()

    audit_service.record_role_change(
        db, actor=actor, target=target, from_code=from_code, to_code=role_code
    )
    db.commit()

    logger.warning(
        "角色变更：%s(%s) 把 %s(%s) 从 %s 改成 %s",
        actor.username, actor.id, target.username, target.id, from_code, role_code,
    )
    return {
        "id": target.id,
        "username": target.username,
        "role_code": role_code,
        "changed": True,
    }


def ensure_unique(db: Session, *, username: str, email: str) -> None:
    """用户名 / 邮箱的唯一性检查。

    **注册（`auth_service.register`）与管理员开通（`create_user`）共用这一份** ——
    两处各写一遍的话，哪天加了"用户名不允许连续下划线"这类新规则，
    很容易只改一处，于是同一个名字走两条路一条能过一条不能过，
    而报错信息还会在两边不一致。
    """
    if user_repository.get_by_username(db, username) is not None:
        raise AppException(ErrorCode.PARAM_ERROR, "用户名已被占用")
    if user_repository.get_by_email(db, email) is not None:
        raise AppException(ErrorCode.PARAM_ERROR, "邮箱已被注册")


def create_user(db: Session, *, actor: User, payload) -> User:
    """管理员开通账号。

    它和自助注册**不是**同一件事，所以两条路都留着：
      · 自助注册（`auth_service.register`）写死 employee，且受 `AUTH_ALLOW_REGISTER` 门控；
      · 这里由管理员发起，可以**一开始就指定角色** —— 否则建完还得再调一次改角色接口，
        中间那一步"新账号已经有了 employee 权限"是个没必要的窗口。

    与改角色一样**必须写审计**：这是唯一一条"创造出新身份"的记录。
    """
    role = role_repository.get_by_code(db, payload.role_code)
    if role is None:
        # 与 change_role 一致：入参不合法给 40000，不给 40400
        raise AppException(ErrorCode.PARAM_ERROR, f"角色不存在：{payload.role_code}")

    ensure_unique(db, username=payload.username, email=payload.email)

    user = user_repository.create(
        db,
        username=payload.username,
        email=payload.email,
        password_hash=hash_password(payload.password),
        role_id=role.id,
    )
    # repository 内部已 flush（拿得到 id），这里直接记审计
    audit_service.record_user_create(db, actor=actor, target=user, role_code=role.code)
    db.commit()

    logger.warning(
        "开通账号：%s(%s) 新建了 %s(%s)，角色 %s",
        actor.username, actor.id, user.username, user.id, role.code,
    )
    return user


def change_status(db: Session, *, actor: User, target_id: int, is_active: bool) -> dict:
    """启用 / 停用账号。

    **停用是"收回访问权"里最重的一档** —— 比降级角色更彻底：角色降了还能登录，
    停用之后连登录都进不来（`auth_service.login` 与 `get_current_user` 都会拒绝），
    而且**已签发的 token 会在下一次请求立刻失效**（鉴权每次都回数据库查 `is_active`，
    不信任 token 里的内容）。

    两条约束（与改角色那三条同源，都是"别把自己锁在门外"）：

    1. **不能停用自己** —— 用 `id == actor.id` 判，与"不能改自己的角色"同理。
       这条**在界面上是能真的触发到的**（管理员点了自己那一行的按钮）。
    2. **不能让 admin 归零** —— 与改角色的第 2 条同款，同样用
       `count_admins(for_update=True)` 加锁，理由见那个函数的 docstring。
       与改角色一样，这条**在 HTTP 层触发不到**（要停用 A 就得有 admin B 操作，
       那时至少两个 admin），是纵深防御，别当死代码删。

    ⚠️ 计数用的是 `count_admins`，它数"角色是 admin 的所有人"，**不区分是否已停用**。
    理论上因此存在一个夹缝：A、B 都是 admin 且 B 已被停用，A 把自己降级 ——
    校验会用"还有个 B"放行，而 B 其实登不进来。**这个夹缝在接口层够不到**
    （降级自己先被"不能改自己"挡住），要精确堵它得让 count_admins 按
    `is_active` 过滤，而那会让"最后一个 admin"这条闸门在别的路径上变松。
    取舍：保持简单，把已知边界写在这里而不是假装它不存在。
    """
    target = user_repository.get_by_id(db, target_id)
    if target is None:
        raise AppException(ErrorCode.NOT_FOUND, "用户不存在")

    if target.id == actor.id:
        raise AppException(
            ErrorCode.PARAM_ERROR, "不能停用自己的账号（会被锁在系统外）"
        )

    if target.is_active == is_active:
        # 幂等：重复点"停用"直接当成功返回，不当错误
        return {
            "id": target.id, "username": target.username,
            "is_active": target.is_active, "changed": False,
        }

    if (
        not is_active
        and target.role
        and target.role.code == PRIVILEGED_ROLE
        and count_admins(db, for_update=True) <= 1
    ):
        raise AppException(
            ErrorCode.PARAM_ERROR,
            "系统里至少要保留一个可用的管理员（这位是最后一个，不能停用）",
        )

    from_active = target.is_active
    target.is_active = is_active
    db.flush()

    audit_service.record_user_status(
        db, actor=actor, target=target, from_active=from_active, to_active=is_active
    )
    db.commit()

    logger.warning(
        "账号状态：%s(%s) 把 %s(%s) 改成 %s",
        actor.username, actor.id, target.username, target.id,
        "启用" if is_active else "停用",
    )
    return {
        "id": target.id,
        "username": target.username,
        "is_active": target.is_active,
        "changed": True,
    }


def reset_password(db: Session, *, actor: User, target_id: int, new_password: str) -> dict:
    """管理员重置某人的密码。

    **允许改自己** —— 与"不能改自己的角色 / 不能停用自己"刻意不同：改密码**不构成自锁**，
    因为你知道自己刚设的新密码。反过来禁掉它，会让"管理员忘了密码"变成只能改数据库，
    那才是真的把自己锁在门外。

    两个立即生效的后果（前端要配合提示）：
      · 旧密码失效（bcrypt 每次换盐，哈希必然不同）；
      · **已签发的 token 全部失效** —— token 里的密码指纹对不上了
        （见 `security.password_fingerprint`）。所以重置**自己**的密码之后，
        当前这个会话也会在下一个请求被踢回登录页。

    ⚠️ 不检查 `is_active`：停用的账号也能被重置（先重置、再启用是合理顺序）。
    """
    target = user_repository.get_by_id(db, target_id)
    if target is None:
        raise AppException(ErrorCode.NOT_FOUND, "用户不存在")

    target.password_hash = hash_password(new_password)
    db.flush()

    audit_service.record_user_password_reset(db, actor=actor, target=target)
    db.commit()

    logger.warning(
        "重置密码：%s(%s) 重置了 %s(%s) 的密码%s",
        actor.username, actor.id, target.username, target.id,
        "（是他自己）" if target.id == actor.id else "",
    )
    return {
        "id": target.id,
        "username": target.username,
        # 前端据此决定"要不要立刻把自己踢回登录页"
        "is_self": target.id == actor.id,
    }


def list_grants(db: Session, user_id: int) -> list[dict]:
    """某人被授权了哪些知识库。

    管理员用它回答"这个人现在能看什么" —— 此前这个信息只能反方向查
    （在知识库页一个个点开成员弹窗），有了它才能从"人"这一侧看权限全貌。
    """
    target = user_repository.get_by_id(db, user_id)
    if target is None:
        raise AppException(ErrorCode.NOT_FOUND, "用户不存在")

    rows = (
        db.execute(
            select(KnowledgeBaseMember)
            .where(KnowledgeBaseMember.user_id == user_id)
            .order_by(KnowledgeBaseMember.id.desc())
        )
        .scalars()
        .all()
    )

    out = []
    for m in rows:
        kb = knowledge_repository.get_by_id(db, m.knowledge_base_id)
        out.append({
            # 库可能已被删除（成员行由数据库级联清掉，这里只是防御性判空）
            "knowledge_base_id": m.knowledge_base_id,
            "knowledge_base_name": kb.name if kb else None,
            "permission": m.permission,
            "granted_at": m.created_at,
        })
    return out
