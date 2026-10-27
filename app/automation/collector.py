"""采集任务的编排核心 —— 与"用哪个浏览器"无关，可被单测完整覆盖。

一次采集任务的步骤（每一步都落 `rpa_task_steps`，带时间和截图）：
    login           登录模拟平台
    open_products   打开商品列表
    collect:{sku}   逐个 SKU 采集（平台缺这个 SKU 时记失败步骤，不中断其他 SKU）
    write_snapshots 把采集到的可用/锁定库存写成本地 InventorySnapshot
    run_inspection  触发确定性巡检（拿到新快照后重算阻断项）

三个刻意的设计：
  1. **部分成功不伪装成整体失败**：平台缺某个 SKU 是常态（运营改了商品），
     采集动作本身仍算成功，缺失清单放进 `result.missing_sku_codes` 并在步骤里留痕；
     只有"一个都没采到"才判整体失败（worker 会置 failed）。
  2. **巡检失败不连坐采集**：快照已经写进去了，巡检是增值动作；它失败记一条失败步骤，
     但任务仍算成功 —— 否则重试会把快照再写一遍，看起来像数据错乱。
  3. **步骤即时 commit**：前端要能看到"跑到第几步了"，不能等任务结束才一次性写。
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import settings
from app.models.rpa import RpaTask
from app.models.user import User
from app.repositories import live_commerce_repository as commerce_repo
from app.repositories import rpa_repository as repo
from app.automation.page import PlatformPage, PlatformSkuRow
from app.services import live_commerce_service

STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
# 快照来源标记：巡检/报表里据此区分"人工录入"与"RPA 采集"
SNAPSHOT_SOURCE = "rpa:mock-platform"


class CollectionError(Exception):
    """采集链路整体失败（登录失败 / 一个都没采到）—— worker 据此置任务 failed。"""


def _write_step(
    db: Session,
    task: RpaTask,
    *,
    code: str,
    name: str,
    status: str,
    started_at: datetime,
    message: str | None = None,
    screenshot_path: str | None = None,
) -> None:
    repo.create_step(
        db,
        task_id=task.id,
        step_code=code[:64],
        step_name=name[:128],
        status=status,
        message=(message or None) and message[:512],
        screenshot_path=screenshot_path,
        started_at=started_at,
        completed_at=datetime.now(),
    )
    db.commit()


def _shot(page: PlatformPage, name: str) -> str | None:
    """截图失败不能拖垮采集（页面可能已崩）—— 返回 None，步骤照样留痕。"""
    try:
        return page.screenshot(name)
    except Exception:  # noqa: BLE001 - 截图是证据，不是业务
        return None


def execute_collection_task(db: Session, page: PlatformPage, task: RpaTask) -> dict:
    """执行一次采集，返回写进 `rpa_tasks.result` 的结构。

    抛 `CollectionError` 表示整体失败（worker 会把任务置 failed）。
    """
    started = datetime.now()
    t0 = datetime.now()

    # ---- 1. 登录 ----
    try:
        page.login(settings.MOCK_PLATFORM_USERNAME, settings.MOCK_PLATFORM_PASSWORD)
    except Exception as exc:  # noqa: BLE001 - 浏览器异常种类多，统一转业务失败
        _write_step(
            db, task, code="login", name="登录模拟电商平台", status=STEP_FAILED,
            started_at=t0, message=f"登录失败：{exc}"[:500],
            screenshot_path=_shot(page, "login_failed"),
        )
        raise CollectionError(f"登录平台失败：{exc}") from exc
    _write_step(
        db, task, code="login", name="登录模拟电商平台", status=STEP_SUCCEEDED,
        started_at=t0, message="登录成功", screenshot_path=_shot(page, "login"),
    )

    # ---- 2. 读取商品列表 ----
    t1 = datetime.now()
    try:
        rows = page.read_products()
    except Exception as exc:  # noqa: BLE001
        _write_step(
            db, task, code="open_products", name="打开商品列表", status=STEP_FAILED,
            started_at=t1, message=f"读取商品页失败：{exc}"[:500],
            screenshot_path=_shot(page, "products_failed"),
        )
        raise CollectionError(f"读取平台商品页失败：{exc}") from exc
    _write_step(
        db, task, code="open_products", name="打开商品列表", status=STEP_SUCCEEDED,
        started_at=t1, message=f"平台返回 {len(rows)} 个 SKU",
        screenshot_path=_shot(page, "products"),
    )

    by_code = {row.sku_code: row for row in rows}
    wanted: list[str] = list((task.payload or {}).get("sku_codes") or [])

    # ---- 3. 逐 SKU 采集（缺失不中断）----
    collected: list[PlatformSkuRow] = []
    missing: list[str] = []
    for code in wanted:
        step_at = datetime.now()
        row = by_code.get(code)
        if row is None:
            missing.append(code)
            _write_step(
                db, task, code=f"collect:{code}", name=f"采集 SKU {code}", status=STEP_FAILED,
                started_at=step_at,
                message="平台商品列表中未找到该 SKU（可能已删除或换码）",
            )
            continue
        collected.append(row)
        _write_step(
            db, task, code=f"collect:{code}", name=f"采集 SKU {code}", status=STEP_SUCCEEDED,
            started_at=step_at,
            message=(
                f"状态={row.platform_status} 标价={row.list_price} "
                f"直播价={row.live_price or '-'} 可用={row.available_quantity} "
                f"锁定={row.reserved_quantity}"
            )[:512],
        )

    if not collected:
        raise CollectionError(
            f"本次请求的 {len(wanted)} 个 SKU 一个都没采到（缺失：{', '.join(missing[:5]) or '-'}）"
        )

    # ---- 4. 写本地库存快照（巡检读的就是它）----
    snap_at = datetime.now()
    snapshots: list[dict] = []
    unlinked: list[str] = []
    for row in collected:
        sku = commerce_repo.get_sku_by_code(db, row.sku_code)
        if sku is None:
            # 平台有、本地没有：映射缺失，记下来让运营核对，不算整体失败
            unlinked.append(row.sku_code)
            continue
        snapshot = commerce_repo.create_inventory_snapshot(
            db,
            sku_id=sku.id,
            available_quantity=row.available_quantity,
            reserved_quantity=row.reserved_quantity,
            source=SNAPSHOT_SOURCE,
            captured_at=snap_at,
            created_by=task.requested_by,
        )
        snapshots.append({
            "sku_code": row.sku_code,
            "snapshot_id": snapshot.id,
            "available_quantity": row.available_quantity,
            "reserved_quantity": row.reserved_quantity,
            "sellable_quantity": max(0, row.available_quantity - row.reserved_quantity),
        })
    db.commit()
    _write_step(
        db, task, code="write_snapshots", name="写入库存快照",
        status=STEP_SUCCEEDED if snapshots else STEP_FAILED,
        started_at=snap_at,
        message=(
            f"写入 {len(snapshots)} 条快照（来源 {SNAPSHOT_SOURCE}）"
            + (f"；本地未匹配 SKU：{', '.join(unlinked[:5])}" if unlinked else "")
        )[:512],
    )
    if not snapshots:
        raise CollectionError(
            f"采集到的 {len(collected)} 个 SKU 在本地都没有对应映射：{', '.join(unlinked[:5])}"
        )

    # ---- 5. 触发巡检（增值动作，失败不连坐采集）----
    insp_at = datetime.now()
    inspection: dict = {"status": "skipped", "reason": "未找到建任务人，无法执行巡检"}
    user = db.get(User, task.requested_by)
    if user is not None:
        try:
            batch = live_commerce_service.run_inspection(
                db, user, session_id=task.session_id
            )
            inspection = {
                "status": "completed",
                "batch_id": batch.id,
                "conclusion": batch.conclusion,
                "score": batch.score,
                "blocker_count": batch.blocker_count,
                "warning_count": batch.warning_count,
            }
            _write_step(
                db, task, code="run_inspection", name="触发直播前巡检", status=STEP_SUCCEEDED,
                started_at=insp_at,
                message=f"巡检完成：{batch.conclusion}，准备分 {batch.score}",
            )
        except Exception as exc:  # noqa: BLE001 - 巡检失败不该让采集白做
            inspection = {"status": "failed", "error": str(exc)[:300]}
            _write_step(
                db, task, code="run_inspection", name="触发直播前巡检", status=STEP_FAILED,
                started_at=insp_at, message=f"巡检失败：{exc}"[:500],
            )

    return {
        "collected": [
            {
                "sku_code": row.sku_code,
                "title": row.title,
                "platform_status": row.platform_status,
                "list_price": row.list_price,
                "live_price": row.live_price,
                "available_quantity": row.available_quantity,
                "reserved_quantity": row.reserved_quantity,
                "promotion_starts_at": row.promotion_starts_at,
                "promotion_ends_at": row.promotion_ends_at,
                "stackable": row.stackable,
            }
            for row in collected
        ],
        "missing_sku_codes": missing,
        "unlinked_sku_codes": unlinked,
        "snapshots": snapshots,
        "inspection": inspection,
        "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000),
    }


# ==================== 修复任务（审批通过后才会被调度） ====================

REPAIR_PRICE_LABEL = "修改直播价"
REPAIR_DELIST_LABEL = "商品下架"


def _expected_matches(row: PlatformSkuRow, expected: dict) -> bool:
    """按预期值比对平台当前值。字符串比较要规整小数（99.0 与 99.00 是同一个价）。"""
    if "live_price" in expected:
        try:
            actual = Decimal(row.live_price) if row.live_price else None
            want = Decimal(str(expected["live_price"]))
        except (InvalidOperation, TypeError):
            return False
        return actual is not None and actual == want
    if "platform_status" in expected:
        return row.platform_status == expected["platform_status"]
    return False


def execute_repair_task(db: Session, page: PlatformPage, task: RpaTask) -> dict:
    """执行一次**写操作**（改价 / 下架），并在写完后**重新读页面验证**。

    这是与采集任务最本质的区别：采集"读到就是结果"，修复"改完不算完"——
    RPA 的保存可能静默失败（页面报错没抛出、后端拒绝、并发覆盖），
    所以必须重新读平台、按审批单的预期值比对，**验证不过就是失败**，
    并回写审批单为 failed 等人接管（绝不因为"点了保存"就标成功）。

    ⚠️ 该函数只应在审批通过后被调用：payload 里的 approval_id 是审计链的一环，
    缺了它说明调度出了问题 —— 直接判失败，不猜。
    """
    payload = task.payload or {}
    approval_id = payload.get("approval_id")
    sku_code = payload.get("sku_code")
    expected = payload.get("expected") or {}
    if not approval_id or not sku_code or not expected:
        raise CollectionError("修复任务缺少 approval_id / sku_code / expected，拒绝执行")
    label = REPAIR_PRICE_LABEL if "live_price" in expected else REPAIR_DELIST_LABEL
    started = datetime.now()

    # ---- 登录 ----
    t0 = datetime.now()
    try:
        page.login(settings.MOCK_PLATFORM_USERNAME, settings.MOCK_PLATFORM_PASSWORD)
    except Exception as exc:  # noqa: BLE001
        _write_step(
            db, task, code="login", name="登录模拟电商平台", status=STEP_FAILED,
            started_at=t0, message=f"登录失败：{exc}"[:500],
            screenshot_path=_shot(page, "repair_login_failed"),
        )
        raise CollectionError(f"登录平台失败：{exc}") from exc
    _write_step(
        db, task, code="login", name="登录模拟电商平台", status=STEP_SUCCEEDED,
        started_at=t0, message="登录成功", screenshot_path=_shot(page, "repair_login"),
    )

    # ---- 读当前值（before）----
    t1 = datetime.now()
    try:
        rows = {row.sku_code: row for row in page.read_products()}
    except Exception as exc:  # noqa: BLE001
        _write_step(
            db, task, code="open_products", name="打开商品列表", status=STEP_FAILED,
            started_at=t1, message=f"读取商品页失败：{exc}"[:500],
            screenshot_path=_shot(page, "repair_products_failed"),
        )
        raise CollectionError(f"读取平台商品页失败：{exc}") from exc
    before_row = rows.get(sku_code)
    if before_row is None:
        _write_step(
            db, task, code="open_products", name="打开商品列表", status=STEP_FAILED,
            started_at=t1, message=f"平台上找不到 SKU {sku_code}", screenshot_path=_shot(page, "repair_missing"),
        )
        raise CollectionError(f"平台上找不到 SKU {sku_code}，无法执行{label}")
    before = {
        "live_price": before_row.live_price,
        "platform_status": before_row.platform_status,
    }
    _write_step(
        db, task, code="open_products", name="打开商品列表", status=STEP_SUCCEEDED,
        started_at=t1,
        message=f"{sku_code} 当前：直播价={before_row.live_price or '-'} 状态={before_row.platform_status}",
        screenshot_path=_shot(page, "repair_before"),
    )

    # ---- 写入（改价 / 下架）----
    t2 = datetime.now()
    try:
        if "live_price" in expected:
            page.update_price(sku_code, expected["live_price"])
            target_desc = f"直播价 {before_row.live_price or '-'} → {expected['live_price']}"
        else:
            page.set_status(sku_code, expected["platform_status"])
            target_desc = f"状态 {before_row.platform_status} → {expected['platform_status']}"
    except Exception as exc:  # noqa: BLE001
        _write_step(
            db, task, code=f"repair:{sku_code}", name=label, status=STEP_FAILED,
            started_at=t2, message=f"写入失败：{exc}"[:500],
            screenshot_path=_shot(page, "repair_write_failed"),
        )
        raise CollectionError(f"{label}失败：{exc}") from exc
    _write_step(
        db, task, code=f"repair:{sku_code}", name=label, status=STEP_SUCCEEDED,
        started_at=t2, message=target_desc[:512], screenshot_path=_shot(page, "repair_after_write"),
    )

    # ---- 二次读取验证（这一步才是"改成功"的唯一判据）----
    t3 = datetime.now()
    try:
        after_rows = {row.sku_code: row for row in page.read_products()}
    except Exception as exc:  # noqa: BLE001
        raise CollectionError(f"写入后重新读取页面失败：{exc}") from exc
    after_row = after_rows.get(sku_code)
    if after_row is None:
        _write_step(
            db, task, code=f"verify:{sku_code}", name="执行后验证", status=STEP_FAILED,
            started_at=t3, message="写入后页面上找不到该 SKU（可能被删除）",
            screenshot_path=_shot(page, "repair_verify_missing"),
        )
        raise CollectionError("执行后验证失败：页面上找不到目标 SKU")
    actual = {
        "live_price": after_row.live_price,
        "platform_status": after_row.platform_status,
    }
    if not _expected_matches(after_row, expected):
        _write_step(
            db, task, code=f"verify:{sku_code}", name="执行后验证", status=STEP_FAILED,
            started_at=t3,
            message=f"验证不通过：平台实际值 {actual} 与预期 {expected} 不一致"[:512],
            screenshot_path=_shot(page, "repair_verify_failed"),
        )
        raise CollectionError(
            f"执行后验证不通过：预期 {expected}，平台实际 {actual}（保存可能被平台拒绝）"
        )
    _write_step(
        db, task, code=f"verify:{sku_code}", name="执行后验证", status=STEP_SUCCEEDED,
        started_at=t3, message=f"验证通过：平台实际值 {actual}"[:512],
        screenshot_path=_shot(page, "repair_verified"),
    )

    return {
        "approval_id": approval_id,
        "sku_code": sku_code,
        "before": before,
        "expected": expected,
        "actual": actual,
        "verified": True,
        "elapsed_ms": int((datetime.now() - started).total_seconds() * 1000),
    }
