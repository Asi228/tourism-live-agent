"""RPA 队列消费者 —— 独立进程运行：`python -m app.automation.worker`。

为什么独立进程而不是 FastAPI 后台任务：
  · 浏览器启动慢（秒级）、占用内存大，塞进 API 进程会拖慢整个服务；
  · 崩溃隔离：Chromium 崩了不该带走 Web 服务；
  · 扩容独立：worker 可以单独多开，API 不用跟着扩。

与 API 的契约（全部通过 Redis + 数据库，无进程内共享）：
  · 生产者（rpa_service.create_collection_task / requeue_task）RPUSH 任务 id；
  · worker BLPOP 取 id → 执行 → 回写 `rpa_tasks` 状态与 `rpa_task_steps` 步骤；
  · 同一任务用 Redis SET NX 锁防重复执行（多开 worker 时也不会并发跑同一个任务）。
"""

import logging
import signal
import sys
import time
from datetime import datetime

from app.config import settings
from app.core.redis_client import get_client
from app.db.session import new_session
from app.models.rpa import (
    APPROVAL_EXECUTED,
    APPROVAL_FAILED,
    TASK_COLLECT,
    TASK_FAILED,
    TASK_PRICE_FIX,
    TASK_QUEUED,
    TASK_RUNNING,
    TASK_SUCCEEDED,
)
from app.repositories import rpa_repository as repo
from app.automation.collector import (
    CollectionError,
    execute_collection_task,
    execute_repair_task,
)
from app.automation.playwright_page import PlaywrightPlatformPage

logger = logging.getLogger(__name__)

# 与 app/services/rpa_service.py 的 RPA_QUEUE_KEY 必须逐字一致（生产/消费同一队列）
QUEUE_KEY = "enterprise-agent:rpa:tasks"
LOCK_KEY = "enterprise-agent:rpa:lock:{task_id}"
LOCK_TTL_SECONDS = 600
# 共享 Redis 客户端的 socket_timeout 是 2 秒；BLPOP 必须更短，否则队列空闲会
# 被 redis-py 当成网络超时并不断打印错误日志。1 秒也让 Ctrl+C 停止足够及时。
QUEUE_POLL_SECONDS = 1
RUNNING = True


def _acquire_lock(task_id: int) -> bool:
    return bool(
        get_client().set(
            LOCK_KEY.format(task_id=task_id), "1", nx=True, ex=LOCK_TTL_SECONDS
        )
    )


def _release_lock(task_id: int) -> None:
    try:
        get_client().delete(LOCK_KEY.format(task_id=task_id))
    except Exception:  # noqa: BLE001 - 锁过期自己会走，删不掉不影响正确性
        logger.warning("释放 RPA 任务锁失败 task=%s", task_id)


def _run_task(db, page, task):
    """按任务类型分派：采集是只读，修复是写（必须带审批单）。"""
    if task.task_type == TASK_COLLECT:
        return execute_collection_task(db, page, task)
    if task.task_type in (TASK_PRICE_FIX, "delist"):
        return execute_repair_task(db, page, task)
    raise CollectionError(f"未知任务类型：{task.task_type}")


def _finalize_approval(db, task, *, ok: bool) -> None:
    """把执行结果回写到审批单：executed / failed。

    修复任务一定带 approval_id（collector 缺它就拒绝执行）；采集任务没有，直接跳过。
    回写放在**任务状态更新之后**：先确认任务落库，再改审批单 —— 顺序反了的话，
    任务写失败时审批单已经显示已执行，审计链就断了。
    """
    approval_id = (task.payload or {}).get("approval_id")
    if not approval_id:
        return
    row = repo.get_approval(db, int(approval_id))
    if row is None:
        return
    row.status = APPROVAL_EXECUTED if ok else APPROVAL_FAILED
    db.commit()


def process_task(task_id: int) -> str:
    """执行一个任务，返回结果标签（供日志与测试断言）。"""
    if not _acquire_lock(task_id):
        logger.info("任务 %s 已被其他 worker 执行中，跳过", task_id)
        return "locked"
    try:
        with new_session() as db:
            task = repo.get_task(db, task_id)
            if task is None:
                logger.warning("任务 %s 不存在（可能已被删除）", task_id)
                return "not_found"
            # Redis 至少一次投递会出现重复消息：例如用户在任务仍为 queued 时重复点击
            # “重新入队”，或 worker 成功落库后旧消息才被消费。终态任务必须直接跳过，
            # 否则只读采集会重复写快照，高风险修复甚至可能重复操作平台。
            if task.status == TASK_SUCCEEDED:
                logger.info("任务 %s 已成功完成，忽略重复队列消息", task_id)
                return "already_succeeded"
            if task.status not in {TASK_QUEUED, TASK_RUNNING}:
                logger.info(
                    "任务 %s 当前状态为 %s，忽略过期队列消息", task_id, task.status
                )
                return "stale_message"
            if task.status == TASK_RUNNING:
                # 锁已保证不会并发；这里防的是"上一个 worker 崩溃后残留 running"
                logger.warning("任务 %s 状态为 running，按残留处理并重新执行", task_id)
            task.status = TASK_RUNNING
            task.started_at = datetime.now()
            task.attempt_count = (task.attempt_count or 0) + 1
            task.error_message = None
            db.commit()

            shot_dir = f"{settings.RPA_SCREENSHOT_DIR}/{task_id}"
            try:
                with PlaywrightPlatformPage(settings.MOCK_PLATFORM_BASE_URL, shot_dir) as page:
                    result = _run_task(db, page, task)
            except CollectionError as exc:
                # 业务失败：步骤已落库（collector 每步 commit），这里只收尾任务状态
                db.rollback()
                task = repo.get_task(db, task_id)
                task.status = TASK_FAILED
                task.error_message = str(exc)[:500]
                task.completed_at = datetime.now()
                db.commit()
                _finalize_approval(db, task, ok=False)
                logger.warning("任务 %s 执行失败：%s", task_id, exc)
                return "failed"
            except Exception as exc:  # noqa: BLE001 - 浏览器/网络异常统一收口
                db.rollback()
                task = repo.get_task(db, task_id)
                task.status = TASK_FAILED
                task.error_message = f"执行异常：{exc}"[:500]
                task.completed_at = datetime.now()
                db.commit()
                _finalize_approval(db, task, ok=False)
                logger.exception("任务 %s 执行异常", task_id)
                return "failed"

            task = repo.get_task(db, task_id)
            task.status = TASK_SUCCEEDED
            task.result = result
            task.completed_at = datetime.now()
            task.error_message = None
            db.commit()
            _finalize_approval(db, task, ok=True)
            # 采集与修复的 result 结构不同，日志各写各的（曾因硬编码 collected 键 KeyError）
            if task.task_type == TASK_COLLECT:
                summary = (
                    f"采集 {len(result.get('collected', []))} 个 SKU，"
                    f"缺失 {len(result.get('missing_sku_codes', []))}，"
                    f"巡检 {result.get('inspection', {}).get('status')}"
                )
            else:
                summary = (
                    f"修复 {result.get('sku_code')}："
                    f"{result.get('before')} -> {result.get('actual')}，"
                    f"验证 {'通过' if result.get('verified') else '未通过'}"
                )
            logger.info("任务 %s 完成 type=%s：%s", task_id, task.task_type, summary)
            return "succeeded"
    finally:
        _release_lock(task_id)


def run_forever() -> None:
    """阻塞消费队列。Ctrl+C 优雅退出（不杀正在执行的任务）。"""
    global RUNNING
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    logger.info("RPA worker 启动，监听队列 %s（Ctrl+C 退出）", QUEUE_KEY)
    while RUNNING:
        try:
            item = get_client().blpop(QUEUE_KEY, timeout=QUEUE_POLL_SECONDS)
        except Exception as exc:  # noqa: BLE001 - Redis 短暂不可用时不要退出进程
            logger.warning("读取队列失败（%.1fs 后重试）：%s", 3.0, exc)
            time.sleep(3)
            continue
        if not item:
            continue
        try:
            task_id = int(item[1])
        except (TypeError, ValueError):
            logger.warning("忽略非法队列消息：%r", item)
            continue
        process_task(task_id)
    logger.info("RPA worker 已停止")


def _handle_stop(signum, frame) -> None:  # noqa: ANN001 - signal 处理器签名固定
    global RUNNING
    logger.info("收到退出信号 %s，处理完当前任务后停止", signum)
    RUNNING = False


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [rpa-worker] %(message)s",
    )
    try:
        import app.models  # noqa: F401 - 确保所有模型已加载（repo 关系解析）
    except ImportError:
        sys.exit("模型加载失败，检查 PYTHONPATH 是否包含仓库根")
    run_forever()
