"""创建可重复演示的直播场次，注入平台异常并发起一次真实 RPA 采集。"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.models  # noqa: E402,F401
from app.config import settings
from app.db.session import new_session
from app.models.role import Role
from app.models.rpa import TASK_FAILED, TASK_QUEUE_FAILED, TASK_SUCCEEDED
from app.models.user import User
from app.repositories import live_commerce_repository as commerce_repo
from app.repositories import rpa_repository as rpa_repo
from app.services import live_commerce_service, rpa_service


SKU_CODE = "TRIP-HOTEL-SANYA-2N"


def _require_platform() -> None:
    url = f"{settings.MOCK_PLATFORM_BASE_URL.rstrip('/')}/login"
    try:
        with httpx.Client(trust_env=False, timeout=5, follow_redirects=True) as client:
            response = client.get(url)
        if response.status_code >= 500:
            raise RuntimeError(f"HTTP {response.status_code}")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "模拟平台不可访问，请先启动 API（推荐 python scripts/start_stack.py）"
        ) from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="创建并运行直播电商 RPA 演示")
    parser.add_argument("--no-wait", action="store_true", help="入队后立即返回")
    parser.add_argument("--timeout", type=int, default=120, help="等待 worker 的秒数")
    args = parser.parse_args()
    _require_platform()

    now = datetime.now()
    with new_session() as db:
        admin = db.scalar(
            select(User).join(Role).where(Role.code == "admin", User.is_active.is_(True)).limit(1)
        )
        if admin is None:
            print("[FAIL] 没有可用管理员，请先配置 ADMIN_USERNAME/ADMIN_PASSWORD 并启动 API。")
            return 1
        sku = commerce_repo.get_sku_by_code(db, SKU_CODE)
        if sku is None:
            product = live_commerce_service.create_product(
                db, admin, name="三亚海景酒店套餐", brand="海屿度假", category="酒店套餐"
            )
            sku = live_commerce_service.create_sku(
                db, admin, product_id=product.id, sku_code=SKU_CODE,
                specification="豪华海景房2晚含双早", list_price=Decimal("2699.00"), currency="CNY",
            )
        session = live_commerce_service.create_session(
            db, admin,
            title=f"酒店套餐 RPA 异常演示 {now:%m-%d %H:%M}",
            starts_at=now + timedelta(hours=2),
        )
        live_commerce_service.add_session_item(
            db, admin, session_id=session.id, sku_id=sku.id,
            planned_sales=30, planned_unit_price=Decimal("1999.00"), sort_order=1,
        )
        mock = rpa_repo.get_mock_sku_by_code(db, SKU_CODE)
        rpa_service.save_mock_sku(
            db, admin, row_id=mock.id if mock else None,
            sku_code=SKU_CODE,
            title="三亚海景酒店豪华海景房2晚套餐",
            platform_status="active",
            list_price=Decimal("2699.00"),
            live_price=Decimal("2199.00"),
            available_quantity=18,
            reserved_quantity=10,
            promotion_starts_at=now - timedelta(hours=1),
            promotion_ends_at=now + timedelta(days=1),
            stackable=False,
        )
        task = rpa_service.create_collection_task(db, admin, session_id=session.id)
        task_id, session_id = task.id, session.id

    print(f"演示场次已创建：session={session_id}，采集任务={task_id}")
    print("预置异常：计划销量 30、可售房量 8；计划价 1999 元、平台直播价 2199 元。")
    if task.status == TASK_QUEUE_FAILED:
        print(f"[FAIL] Redis 入队失败：{task.error_message}")
        return 1
    if args.no_wait:
        print("任务已入队，请在“自动化任务”页面查看执行过程。")
        return 0

    deadline = time.monotonic() + max(10, args.timeout)
    last_status = None
    while time.monotonic() < deadline:
        with new_session() as db:
            current = rpa_repo.get_task(db, task_id)
            status = current.status
            error = current.error_message
            result = current.result or {}
        if status != last_status:
            print(f"任务状态：{status}")
            last_status = status
        if status == TASK_SUCCEEDED:
            inspection = result.get("inspection") or {}
            print(
                "[OK] RPA 采集和自动巡检完成："
                f"结论={inspection.get('conclusion')}，准备分={inspection.get('score')}，"
                f"阻断={inspection.get('blocker_count')}。"
            )
            print("下一步：打开直播任务查看风险 → Agent 解释 → 生成审批草稿 → 管理员审批。")
            return 0
        if status in {TASK_FAILED, TASK_QUEUE_FAILED}:
            print(f"[FAIL] 任务执行失败：{error}")
            return 1
        time.sleep(1)
    print("[FAIL] 等待超时。请确认 RPA Worker 已启动，并运行 scripts/check_runtime.py。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
