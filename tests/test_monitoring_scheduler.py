"""定时监控计划：权限、调度、幂等推进和入队失败留痕。"""

from datetime import datetime, timedelta

from app.models.rpa import MonitoringPlan, RpaTask, TASK_QUEUE_FAILED, TASK_QUEUED
from app.services import rpa_service

from tests.test_rpa_metrics import _seed_owned_session


def test_monitoring_plan_api_is_scoped_and_requires_session_items(
    client, make_user
):
    admin = make_user("admin")
    owner = make_user("employee")
    outsider = make_user("employee")
    session_id, _ = _seed_owned_session(
        client, admin, owner, suffix="PLAN-OWNER"
    )

    created = client.put(
        f"/api/rpa/monitoring-plans/{session_id}",
        json={"enabled": True, "interval_minutes": 15},
        # 管理员可以代负责人配置，但计划仍归场次负责人可见。
        headers=admin["headers"],
    ).json()
    assert created["code"] == 0, created
    assert created["data"]["session_id"] == session_id
    assert created["data"]["interval_minutes"] == 15
    assert created["data"]["enabled"] is True

    owner_rows = client.get(
        "/api/rpa/monitoring-plans", headers=owner["headers"]
    ).json()["data"]
    outsider_rows = client.get(
        "/api/rpa/monitoring-plans", headers=outsider["headers"]
    ).json()["data"]
    admin_rows = client.get(
        "/api/rpa/monitoring-plans", headers=admin["headers"]
    ).json()["data"]
    assert [row["session_id"] for row in owner_rows] == [session_id]
    assert outsider_rows == []
    assert [row["session_id"] for row in admin_rows] == [session_id]

    denied = client.put(
        f"/api/rpa/monitoring-plans/{session_id}",
        json={"enabled": False, "interval_minutes": 15},
        headers=outsider["headers"],
    ).json()
    assert denied["code"] == 40400


def test_scheduler_dispatches_due_plan_once_and_advances_schedule(
    client, make_user, session_factory, monkeypatch
):
    admin = make_user("admin")
    owner = make_user("employee")
    session_id, _ = _seed_owned_session(
        client, admin, owner, suffix="PLAN-DUE"
    )
    body = client.put(
        f"/api/rpa/monitoring-plans/{session_id}",
        json={"enabled": True, "interval_minutes": 5},
        headers=owner["headers"],
    ).json()
    plan_id = body["data"]["id"]
    now = datetime.now().replace(microsecond=0)

    pushed = []
    monkeypatch.setattr(rpa_service, "_push_task", pushed.append)
    with session_factory() as db:
        plan = db.get(MonitoringPlan, plan_id)
        plan.next_run_at = now - timedelta(minutes=12)
        scheduled_for = plan.next_run_at
        db.commit()

        queued = rpa_service.dispatch_due_monitoring_plans(db, now=now)
        assert queued == pushed
        assert len(queued) == 1

        task = db.get(RpaTask, queued[0])
        db.refresh(plan)
        assert task.status == TASK_QUEUED
        assert task.requested_by == owner["id"]
        assert task.payload["monitoring_plan_id"] == plan_id
        assert task.payload["scheduled_for"] == scheduled_for.isoformat()
        assert task.payload["sku_codes"] == ["METRIC-PLAN-DUE"]
        assert plan.last_task_id == task.id
        assert plan.last_run_at == now
        assert plan.next_run_at > now

        # 同一个时间点再次扫描时计划已推进，不会重复创建或重复入队。
        assert rpa_service.dispatch_due_monitoring_plans(db, now=now) == []
        assert pushed == [task.id]
        assert db.query(RpaTask).count() == 1


def test_scheduler_marks_queue_failure_for_manual_retry(
    client, make_user, session_factory, monkeypatch
):
    admin = make_user("admin")
    owner = make_user("employee")
    session_id, _ = _seed_owned_session(
        client, admin, owner, suffix="PLAN-REDIS"
    )
    plan_id = client.put(
        f"/api/rpa/monitoring-plans/{session_id}",
        json={"enabled": True, "interval_minutes": 5},
        headers=owner["headers"],
    ).json()["data"]["id"]
    now = datetime.now().replace(microsecond=0)

    def fail_push(_task_id):
        raise ConnectionError("Redis unavailable")

    monkeypatch.setattr(rpa_service, "_push_task", fail_push)
    with session_factory() as db:
        plan = db.get(MonitoringPlan, plan_id)
        plan.next_run_at = now
        db.commit()

        assert rpa_service.dispatch_due_monitoring_plans(db, now=now) == []
        db.refresh(plan)
        task = db.get(RpaTask, plan.last_task_id)
        assert task.status == TASK_QUEUE_FAILED
        assert "Redis unavailable" in task.error_message
        assert plan.next_run_at > now
