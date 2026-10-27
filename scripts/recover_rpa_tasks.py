"""预览或恢复可安全重新入队的 RPA 任务；默认只读，不修改数据库。"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import or_, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.models  # noqa: E402,F401
from app.core.redis_client import get_client
from app.db.session import new_session
from app.models.rpa import (
    RpaTask,
    TASK_COLLECT,
    TASK_QUEUE_FAILED,
    TASK_QUEUED,
    TASK_RUNNING,
)
from app.services.rpa_service import RPA_QUEUE_KEY


LOCK_KEY = "enterprise-agent:rpa:lock:{task_id}"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="恢复 Redis 入队失败任务与无锁的陈旧只读采集任务"
    )
    parser.add_argument("--stale-minutes", type=int, default=15)
    parser.add_argument("--apply", action="store_true", help="执行恢复；不加时仅预览")
    args = parser.parse_args()
    if args.stale_minutes < 5:
        parser.error("--stale-minutes 不能小于 5")

    cutoff = datetime.now() - timedelta(minutes=args.stale_minutes)
    redis = get_client()
    with new_session() as db:
        queue_messages = redis.lrange(RPA_QUEUE_KEY, 0, -1)
        queue_ids = {int(value) for value in queue_messages if str(value).isdigit()}
        queue_rows = {
            row.id: row.status
            for row in db.scalars(select(RpaTask).where(RpaTask.id.in_(queue_ids)))
        } if queue_ids else {}
        stale_messages = [
            value for value in queue_messages
            if not str(value).isdigit()
            or queue_rows.get(int(value)) not in {TASK_QUEUED, TASK_RUNNING}
        ]
        active_queue_ids = {
            int(value) for value in queue_messages
            if str(value).isdigit()
            and queue_rows.get(int(value)) in {TASK_QUEUED, TASK_RUNNING}
        }
        for value in stale_messages:
            print(f"[STALE-QUEUE] message={value!r} 对应任务不存在或已非待执行状态")

        rows = list(db.scalars(
            select(RpaTask).where(or_(
                RpaTask.status == TASK_QUEUE_FAILED,
                (
                    (RpaTask.status == TASK_RUNNING)
                    & (RpaTask.task_type == TASK_COLLECT)
                    & (RpaTask.started_at < cutoff)
                ),
            )).order_by(RpaTask.id)
        ))
        safe = []
        for row in rows:
            if redis.exists(LOCK_KEY.format(task_id=row.id)):
                print(f"[SKIP] task={row.id} 仍持有 worker 锁，可能正在执行")
                continue
            reason = "Redis 入队失败" if row.status == TASK_QUEUE_FAILED else "只读采集超时且无锁"
            print(f"[CANDIDATE] task={row.id} type={row.task_type} status={row.status} reason={reason}")
            safe.append(row)

        if not safe and not stale_messages:
            print("没有可安全自动恢复的任务，也没有过期队列消息。")
            return 0
        if not args.apply:
            print(
                f"预览完成：{len(safe)} 个恢复候选，{len(stale_messages)} 条过期队列消息。"
                "确认后加 --apply 执行。"
            )
            return 0

        # 先删旧消息，再重新推候选，避免 queue_failed 的旧消息连同新消息一起被 LREM。
        for value in set(stale_messages):
            redis.lrem(RPA_QUEUE_KEY, 0, value)
        if stale_messages:
            print(f"[CLEANED] 已清理 {len(stale_messages)} 条过期队列消息")

        recovered = 0
        for row in safe:
            if redis.exists(LOCK_KEY.format(task_id=row.id)):
                print(f"[SKIP] task={row.id} 执行前发现 worker 锁")
                continue
            row.status = TASK_QUEUED
            row.started_at = None
            row.completed_at = None
            row.error_message = None
            db.commit()
            try:
                if row.id not in active_queue_ids:
                    redis.rpush(RPA_QUEUE_KEY, str(row.id))
                recovered += 1
                message = "复用队列中的现有消息" if row.id in active_queue_ids else "已重新入队"
                print(f"[RECOVERED] task={row.id} {message}")
            except Exception as exc:  # noqa: BLE001
                row.status = TASK_QUEUE_FAILED
                row.error_message = f"恢复入队失败: {exc}"[:500]
                db.commit()
                print(f"[FAILED] task={row.id} Redis 入队失败：{exc}")
        print(
            f"恢复完成：{recovered}/{len(safe)} 个任务已入队，"
            f"清理过期消息 {len(stale_messages)} 条。"
        )
        return 0 if recovered == len(safe) else 1


if __name__ == "__main__":
    raise SystemExit(main())
