"""定时监控调度器：到期计划 → 幂等创建采集任务 → Redis 队列。"""

import logging
import signal
import time

from app.config import settings
from app.core.logging import setup_logging
from app.db.session import new_session
from app.services.rpa_service import dispatch_due_monitoring_plans

logger = logging.getLogger(__name__)
RUNNING = True


def run_forever() -> None:
    global RUNNING
    setup_logging(settings.DEBUG)
    signal.signal(signal.SIGINT, _handle_stop)
    signal.signal(signal.SIGTERM, _handle_stop)
    interval = max(1, settings.RPA_SCHEDULER_POLL_SECONDS)
    logger.info("RPA 监控调度器启动，扫描间隔 %s 秒", interval)
    while RUNNING:
        try:
            with new_session() as db:
                task_ids = dispatch_due_monitoring_plans(db)
            if task_ids:
                logger.info("定时监控已创建任务: %s", task_ids)
        except Exception as exc:  # noqa: BLE001 - 单轮失败不能让调度器退出
            logger.exception("定时监控调度失败：%s", exc)
        time.sleep(interval)
    logger.info("RPA 监控调度器已停止")


def _handle_stop(signum, frame) -> None:  # noqa: ANN001
    global RUNNING
    logger.info("收到退出信号 %s，停止监控调度器", signum)
    RUNNING = False


if __name__ == "__main__":
    run_forever()
