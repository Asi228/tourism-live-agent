"""RPA 采集编排的测试 —— **不开浏览器**（FakePage），但把落库与状态推进全钉住。

盯的是这几件事（都是线上真会踩的）：
  1. 一次成功采集要留下完整证据链：步骤齐全、快照写入、巡检被自动触发；
  2. 平台缺某个 SKU 是常态（运营删了商品），要**部分成功**而不是整体失败；
  3. 一个都没采到 / 登录失败 / 本地无映射 → 明确失败（worker 会置 failed）；
  4. 重试会再写一轮步骤与快照 —— 采集是只读动作，重复执行无害。
"""

import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest

from app.models.rpa import TASK_QUEUED
from app.repositories import live_commerce_repository as commerce_repo
from app.repositories import rpa_repository as rpa_repo
from app.automation.collector import (
    SNAPSHOT_SOURCE,
    STEP_FAILED,
    STEP_SUCCEEDED,
    CollectionError,
    execute_collection_task,
)
from app.automation.page import FakePage, PlatformSkuRow


def _row(**over) -> PlatformSkuRow:
    base = dict(
        sku_code="YL-SH-450",
        title="山茶花修护洗发水",
        platform_status="active",
        list_price="129.00",
        live_price="99.00",
        available_quantity=1000,
        reserved_quantity=100,
        promotion_starts_at="",
        promotion_ends_at="",
        stackable=False,
    )
    base.update(over)
    return PlatformSkuRow(**base)


def _seed_session(client, admin, *, planned_sales: int = 800) -> tuple[int, int]:
    """造一条完整业务链：商品 → SKU → 场次 → 选品。返回 (session_id, sku_id)。"""
    product = client.post(
        "/api/commerce/products",
        json={"name": "山茶花修护洗发水", "brand": "云岚", "category": "洗护"},
        headers=admin["headers"],
    ).json()
    assert product["code"] == 0, product
    sku = client.post(
        f"/api/commerce/products/{product['data']['id']}/skus",
        json={"sku_code": "YL-SH-450", "specification": "450ml", "list_price": "129.00"},
        headers=admin["headers"],
    ).json()
    assert sku["code"] == 0, sku
    session = client.post(
        "/api/commerce/sessions",
        json={
            "title": "双十一洗护专场",
            "starts_at": (datetime.now() + timedelta(days=3)).isoformat(),
        },
        headers=admin["headers"],
    ).json()
    assert session["code"] == 0, session
    sid = session["data"]["id"]
    item = client.post(
        f"/api/commerce/sessions/{sid}/items",
        json={"sku_id": sku["data"]["id"], "planned_sales": planned_sales, "sort_order": 1},
        headers=admin["headers"],
    ).json()
    assert item["code"] == 0, item
    return sid, sku["data"]["id"]


def _task(db, *, session_id: int, requested_by: int, sku_codes: list[str]):
    task = rpa_repo.create_task(
        db,
        session_id=session_id,
        task_type="collect_platform_data",
        status=TASK_QUEUED,
        idempotency_key=uuid.uuid4().hex,
        requested_by=requested_by,
        payload={"session_id": session_id, "sku_codes": sku_codes},
    )
    db.commit()
    return task


def test_collection_writes_steps_snapshots_and_triggers_inspection(
    client, make_user, session_factory
):
    """成功链路：步骤齐全、快照落库、巡检自动触发。"""
    admin = make_user("admin")
    sid, sku_id = _seed_session(client, admin)

    with session_factory() as db:
        task = _task(db, session_id=sid, requested_by=admin["id"], sku_codes=["YL-SH-450"])
        result = execute_collection_task(db, FakePage([_row()]), task)

        steps = rpa_repo.get_task(db, task.id).steps
        assert [s.step_code for s in steps] == [
            "login",
            "open_products",
            "collect:YL-SH-450",
            "write_snapshots",
            "run_inspection",
        ]
        assert all(s.status == STEP_SUCCEEDED for s in steps), [
            (s.step_code, s.status, s.message) for s in steps
        ]
        # 步骤里有截图证据（真实实现是 png 路径，FakePage 是假路径）
        assert steps[0].screenshot_path and "login" in steps[0].screenshot_path

        # 快照：可用 1000 / 锁定 100 → 可售 900；来源标记为 RPA
        snap = commerce_repo.get_latest_inventory(db, sku_id)
        assert snap is not None
        assert (snap.available_quantity, snap.reserved_quantity) == (1000, 100)
        assert snap.sellable_quantity == 900
        assert snap.source == SNAPSHOT_SOURCE
        assert result["snapshots"][0]["sellable_quantity"] == 900

        # 巡检被自动触发，结论与分数落到 result
        assert result["inspection"]["status"] == "completed"
        assert result["inspection"]["conclusion"] in {"ready", "blocked"}
        assert isinstance(result["inspection"]["score"], int)
        # 采集结果本身以 JSON 保留（平台当时的原始数据）
        assert result["collected"][0]["sku_code"] == "YL-SH-450"
        assert result["missing_sku_codes"] == []


def test_missing_platform_sku_is_partial_success(client, make_user, session_factory):
    """平台缺一个 SKU：部分成功，缺失清单进 result 与失败步骤，不整体失败。"""
    admin = make_user("admin")
    sid, _ = _seed_session(client, admin)

    with session_factory() as db:
        task = _task(
            db, session_id=sid, requested_by=admin["id"],
            sku_codes=["YL-SH-450", "GHOST-999"],
        )
        result = execute_collection_task(db, FakePage([_row()]), task)

        assert result["missing_sku_codes"] == ["GHOST-999"]
        assert len(result["snapshots"]) == 1, "采到的那个仍要正常写快照"
        steps = {s.step_code: s for s in rpa_repo.get_task(db, task.id).steps}
        assert steps["collect:GHOST-999"].status == STEP_FAILED
        assert "未找到" in steps["collect:GHOST-999"].message
        assert steps["collect:YL-SH-450"].status == STEP_SUCCEEDED


def test_all_skus_missing_raises(client, make_user, session_factory):
    """一个都没采到 → 整体失败（worker 据此把任务置 failed）。"""
    admin = make_user("admin")
    sid, _ = _seed_session(client, admin)

    with session_factory() as db:
        task = _task(db, session_id=sid, requested_by=admin["id"], sku_codes=["GHOST-999"])
        with pytest.raises(CollectionError, match="一个都没采到"):
            execute_collection_task(db, FakePage([]), task)


def test_login_failure_marks_step_and_raises(client, make_user, session_factory):
    """登录失败：留一条失败步骤（带截图），并抛 CollectionError。"""
    admin = make_user("admin")
    sid, _ = _seed_session(client, admin)

    with session_factory() as db:
        task = _task(db, session_id=sid, requested_by=admin["id"], sku_codes=["YL-SH-450"])
        page = FakePage([_row()], login_error=RuntimeError("账号或密码错误"))
        with pytest.raises(CollectionError, match="登录平台失败"):
            execute_collection_task(db, page, task)

        steps = rpa_repo.get_task(db, task.id).steps
        assert len(steps) == 1
        assert steps[0].step_code == "login"
        assert steps[0].status == STEP_FAILED
        assert "账号或密码错误" in steps[0].message
        assert steps[0].screenshot_path, "登录失败也要留证"


def test_platform_sku_without_local_mapping_is_rejected(
    client, make_user, session_factory
):
    """平台有、本地没映射：采集到但没有可写快照的 SKU → 明确失败，不静默成功。"""
    admin = make_user("admin")
    sid, _ = _seed_session(client, admin)

    with session_factory() as db:
        task = _task(db, session_id=sid, requested_by=admin["id"], sku_codes=["NEW-001"])
        page = FakePage([_row(sku_code="NEW-001")])
        with pytest.raises(CollectionError, match="都没有对应映射"):
            execute_collection_task(db, page, task)

        steps = {s.step_code: s for s in rpa_repo.get_task(db, task.id).steps}
        assert steps["write_snapshots"].status == STEP_FAILED
        assert "NEW-001" in steps["write_snapshots"].message


def test_retry_writes_another_round_of_evidence(client, make_user, session_factory):
    """重试（requeue 后重跑）：再写一轮步骤与快照 —— 采集只读，重复无害。"""
    admin = make_user("admin")
    sid, sku_id = _seed_session(client, admin)

    with session_factory() as db:
        task = _task(db, session_id=sid, requested_by=admin["id"], sku_codes=["YL-SH-450"])
        execute_collection_task(db, FakePage([_row()]), task)
        first_count = len(rpa_repo.get_task(db, task.id).steps)

        execute_collection_task(db, FakePage([_row(available_quantity=980)]), task)
        second = rpa_repo.get_task(db, task.id)
        assert len(second.steps) == first_count * 2, "重试应再留一轮证据，而不是覆盖"
        # 最新快照反映第二次采集的结果
        snap = commerce_repo.get_latest_inventory(db, sku_id)
        assert snap.available_quantity == 980


def test_worker_skips_duplicate_message_for_succeeded_task(
    client, make_user, session_factory, monkeypatch
):
    """Redis 重复投递成功任务时不得再次启动浏览器或写第二轮快照。"""
    from app.automation import worker
    from app.models.rpa import TASK_SUCCEEDED

    admin = make_user("admin")
    sid, _ = _seed_session(client, admin)
    with session_factory() as db:
        task = _task(
            db,
            session_id=sid,
            requested_by=admin["id"],
            sku_codes=["YL-SH-450"],
        )
        task.status = TASK_SUCCEEDED
        task.result = {"collected": [{"sku_code": "YL-SH-450"}]}
        db.commit()
        task_id = task.id

    @contextmanager
    def fake_new_session():
        with session_factory() as db:
            yield db

    monkeypatch.setattr(worker, "new_session", fake_new_session)
    monkeypatch.setattr(worker, "_acquire_lock", lambda _task_id: True)
    monkeypatch.setattr(worker, "_release_lock", lambda _task_id: None)

    class BrowserMustNotStart:
        def __init__(self, *args, **kwargs):
            raise AssertionError("成功任务收到重复消息时不应启动浏览器")

    monkeypatch.setattr(worker, "PlaywrightPlatformPage", BrowserMustNotStart)

    assert worker.process_task(task_id) == "already_succeeded"
    with session_factory() as db:
        stored = rpa_repo.get_task(db, task_id)
        assert stored.status == TASK_SUCCEEDED
        assert stored.attempt_count == 0
        assert stored.steps == []


# ==================== 修复任务（审批通过后的写操作） ====================


def _repair_task(db, *, session_id: int, requested_by: int, sku_code: str, expected: dict, **kw):
    from app.models.rpa import TASK_PRICE_FIX

    task = rpa_repo.create_task(
        db,
        session_id=session_id,
        task_type=kw.get("task_type", TASK_PRICE_FIX),
        status=TASK_QUEUED,
        idempotency_key=uuid.uuid4().hex,
        requested_by=requested_by,
        payload={
            "approval_id": kw.get("approval_id", 1),
            "session_id": session_id,
            "sku_code": sku_code,
            "expected": expected,
        }
        if kw.get("approval_id", 1) is not None
        else {"session_id": session_id, "sku_code": sku_code, "expected": expected},
    )
    db.commit()
    return task


def test_repair_price_writes_and_verifies(client, make_user, session_factory):
    """改价成功：写入 → 重新读页面 → 值等于预期 → 才判成功。"""
    from app.automation.collector import execute_repair_task

    admin = make_user("admin")
    sid, _ = _seed_session(client, admin)

    with session_factory() as db:
        task = _repair_task(
            db, session_id=sid, requested_by=admin["id"],
            sku_code="YL-SH-450", expected={"live_price": "99.00"},
        )
        page = FakePage([_row(live_price="129.00")])
        result = execute_repair_task(db, page, task)

        # 写入被调用了一次，且用的是审批单里的预期值
        assert page.updates == [("price", "YL-SH-450", "99.00")]
        assert result["verified"] is True
        assert result["before"]["live_price"] == "129.00"
        assert result["actual"]["live_price"] == "99.00"
        assert result["expected"] == {"live_price": "99.00"}

        steps = {s.step_code: s for s in rpa_repo.get_task(db, task.id).steps}
        assert set(steps) == {"login", "open_products", "repair:YL-SH-450", "verify:YL-SH-450"}
        assert all(s.status == STEP_SUCCEEDED for s in steps.values())
        assert "129.00" in steps["repair:YL-SH-450"].message
        assert "99.00" in steps["verify:YL-SH-450"].message


def test_repair_verification_failure_raises(client, make_user, session_factory):
    """🔴 保存没生效（平台拒绝/静默失败）→ 验证不通过 → 任务失败，绝不标成功。"""
    from app.automation.collector import execute_repair_task

    admin = make_user("admin")
    sid, _ = _seed_session(client, admin)

    with session_factory() as db:
        task = _repair_task(
            db, session_id=sid, requested_by=admin["id"],
            sku_code="YL-SH-450", expected={"live_price": "99.00"},
        )
        # ignore_writes = 点了保存但平台没生效
        page = FakePage([_row(live_price="129.00")], ignore_writes=True)
        with pytest.raises(CollectionError, match="验证不通过"):
            execute_repair_task(db, page, task)

        steps = {s.step_code: s for s in rpa_repo.get_task(db, task.id).steps}
        assert steps["repair:YL-SH-450"].status == STEP_SUCCEEDED, "写入动作本身是成功的"
        assert steps["verify:YL-SH-450"].status == STEP_FAILED, "验证必须判失败"
        assert "99.00" in steps["verify:YL-SH-450"].message


def test_repair_without_approval_is_rejected(client, make_user, session_factory):
    """缺 approval_id 的修复任务直接拒绝 —— 审计链不能断。"""
    from app.automation.collector import execute_repair_task

    admin = make_user("admin")
    sid, _ = _seed_session(client, admin)

    with session_factory() as db:
        task = _repair_task(
            db, session_id=sid, requested_by=admin["id"],
            sku_code="YL-SH-450", expected={"live_price": "99.00"}, approval_id=None,
        )
        with pytest.raises(CollectionError, match="缺少 approval_id"):
            execute_repair_task(db, FakePage([_row()]), task)
