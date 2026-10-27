"""运行时体检：接口依赖、迁移版本、RPA 队列和疑似卡住任务。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import app.models  # noqa: E402,F401
from app.config import settings
from app.core.redis_client import get_client
from app.db.session import engine, new_session
from app.models.rpa import MonitoringPlan, RpaTask, TASK_QUEUE_FAILED, TASK_QUEUED, TASK_RUNNING


QUEUE_KEY = "enterprise-agent:rpa:tasks"


def collect_runtime_status(base_url: str) -> dict:
    result = {"checked_at": datetime.now().isoformat(timespec="seconds"), "checks": {}}
    checks = result["checks"]
    try:
        with httpx.Client(trust_env=False, timeout=8) as client:
            response = client.get(f"{base_url.rstrip('/')}/api/health")
            body = response.json()
        components = body.get("data") or {}
        checks["api"] = {
            "ok": response.status_code == 200 and body.get("code") == 0,
            "detail": components,
        }
    except Exception as exc:  # noqa: BLE001
        checks["api"] = {"ok": False, "detail": str(exc)[:200]}

    try:
        config = Config(str(ROOT / "alembic.ini"))
        expected = set(ScriptDirectory.from_config(config).get_heads())
        with engine.connect() as connection:
            current = set(MigrationContext.configure(connection).get_current_heads())
        checks["migration"] = {
            "ok": current == expected,
            "detail": {"current": sorted(current), "expected": sorted(expected)},
        }
    except Exception as exc:  # noqa: BLE001
        checks["migration"] = {"ok": False, "detail": str(exc)[:200]}

    now = datetime.now()
    stale_cutoff = now - timedelta(minutes=15)
    overdue_cutoff = now - timedelta(
        seconds=max(60, settings.RPA_SCHEDULER_POLL_SECONDS * 4)
    )
    try:
        redis = get_client()
        queue_messages = redis.lrange(QUEUE_KEY, 0, 999)
        queue_ids = {int(value) for value in queue_messages if str(value).isdigit()}
        with new_session() as db:
            queued = db.scalar(select(func.count()).select_from(RpaTask).where(RpaTask.status == TASK_QUEUED)) or 0
            queue_failed = db.scalar(select(func.count()).select_from(RpaTask).where(RpaTask.status == TASK_QUEUE_FAILED)) or 0
            stale_running = db.scalar(
                select(func.count()).select_from(RpaTask).where(
                    RpaTask.status == TASK_RUNNING,
                    RpaTask.started_at < stale_cutoff,
                )
            ) or 0
            overdue_plans = db.scalar(
                select(func.count()).select_from(MonitoringPlan).where(
                    MonitoringPlan.enabled.is_(True),
                    MonitoringPlan.next_run_at < overdue_cutoff,
                )
            ) or 0
            queued_rows = {
                row.id: row.status
                for row in db.scalars(select(RpaTask).where(RpaTask.id.in_(queue_ids)))
            } if queue_ids else {}
        redis_depth = int(redis.llen(QUEUE_KEY))
        stale_queue_messages = sum(
            not str(value).isdigit()
            or queued_rows.get(int(value)) not in {TASK_QUEUED, TASK_RUNNING}
            for value in queue_messages
        )
        checks["rpa"] = {
            "ok": (
                queue_failed == 0 and stale_running == 0
                and overdue_plans == 0 and stale_queue_messages == 0
            ),
            "warning": True,
            "detail": {
                "db_queued": queued,
                "redis_queue_depth": redis_depth,
                "queue_failed": queue_failed,
                "stale_running_over_15m": stale_running,
                "overdue_monitoring_plans": overdue_plans,
                "stale_queue_messages_sampled": stale_queue_messages,
                "queue_sample_size": len(queue_messages),
            },
        }
    except Exception as exc:  # noqa: BLE001
        checks["rpa"] = {"ok": False, "warning": False, "detail": str(exc)[:200]}
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="检查项目运行状态")
    parser.add_argument("--url", default=f"http://127.0.0.1:{settings.APP_PORT}")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict", action="store_true", help="RPA 积压告警也返回非零退出码")
    args = parser.parse_args()
    result = collect_runtime_status(args.url)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    else:
        for name, item in result["checks"].items():
            state = "OK" if item["ok"] else ("WARN" if item.get("warning") else "FAIL")
            print(f"[{state}] {name}: {item['detail']}")
    critical_failed = any(
        not item["ok"] and not item.get("warning")
        for item in result["checks"].values()
    )
    warning_failed = any(not item["ok"] for item in result["checks"].values())
    return 1 if critical_failed or (args.strict and warning_failed) else 0


if __name__ == "__main__":
    raise SystemExit(main())
