"""直播运营 Agent：业务工具、权限、结构化动作与受约束生成。"""

from datetime import datetime, timedelta


def _seed_session(client, user, *, with_item: bool = True) -> int:
    product = client.post(
        "/api/commerce/products",
        json={"name": "Agent 测试洗发水", "brand": "云岚", "category": "洗护"},
        headers=user["headers"],
    ).json()
    sku = client.post(
        f"/api/commerce/products/{product['data']['id']}/skus",
        json={"sku_code": "AGENT-SKU-1", "specification": "450ml", "list_price": "129.00"},
        headers=user["headers"],
    ).json()
    session = client.post(
        "/api/commerce/sessions",
        json={
            "title": "Agent 风险解释专场",
            "starts_at": (datetime.now() + timedelta(days=2)).isoformat(),
        },
        headers=user["headers"],
    ).json()
    session_id = session["data"]["id"]
    if with_item:
        client.post(
            f"/api/commerce/sessions/{session_id}/items",
            json={
                "sku_id": sku["data"]["id"],
                "planned_sales": 20,
                "planned_unit_price": "99.00",
                "sort_order": 1,
            },
            headers=user["headers"],
        )
        client.post(
            "/api/commerce/inventory",
            json={
                "sku_id": sku["data"]["id"],
                "available_quantity": 100,
                "reserved_quantity": 0,
                "source": "test",
            },
            headers=user["headers"],
        )
    return session_id


def test_live_agent_uses_inspection_and_returns_structured_actions(
    client, make_user, monkeypatch
):
    from app.services import live_agent_service

    admin = make_user("admin")
    session_id = _seed_session(client, admin)
    inspection = client.post(
        f"/api/commerce/sessions/{session_id}/inspections",
        headers=admin["headers"],
    ).json()
    assert inspection["data"]["conclusion"] == "blocked"

    captured = {}

    def fake_search(*args, **kwargs):
        return {
            "reranked": True,
            "results": [{
                "chunk_id": 11,
                "document_id": 7,
                "document_name": "直播价格管理制度.md",
                "knowledge_base_id": 3,
                "page_number": 2,
                "score": 0.91,
                "retrieval_path": "both",
                "content": "直播价格变更必须经过负责人审批。",
            }]
        }

    def fake_chat(system, user, **kwargs):
        captured["system"] = system
        captured["user"] = user
        return {
            "answer": "本场当前不可开播。计划到手价不一致，改价需要审批。[1]",
            "usage": {"input_tokens": 120, "output_tokens": 35, "total_tokens": 155},
            "finish_reason": "stop",
        }

    monkeypatch.setattr(live_agent_service.tools, "search_knowledge", fake_search)
    monkeypatch.setattr(live_agent_service.llm, "chat", fake_chat)
    body = client.post(
        f"/api/agent/live-sessions/{session_id}/explain",
        json={"question": "为什么不能开播？"},
        headers=admin["headers"],
    ).json()

    assert body["code"] == 0, body
    data = body["data"]
    assert data["inspection_id"] == inspection["data"]["id"]
    assert data["conclusion"] == "blocked"
    assert any(risk["rule_code"] == "PRICE_MATCH" for risk in data["risks"])
    price_action = next(
        action for action in data["recommended_actions"]
        if action["rule_code"] == "PRICE_MATCH"
    )
    assert price_action["action_type"] == "request_price_fix"
    assert price_action["requires_approval"] is True
    assert price_action["expected_value"] == {"live_price": "99.00"}
    assert data["sources"][0]["document_name"] == "直播价格管理制度.md"
    assert [step["node"] for step in data["steps"]] == [
        "get_live_session_context", "search_knowledge", "generate_business_answer"
    ]
    assert data["usage"]["total_tokens"] == 155
    assert '"rule_code": "PRICE_MATCH"' in captured["user"]
    assert "只有只读权限" in captured["system"]


def test_live_agent_filters_irrelevant_sources_and_missing_plan_price_action():
    from types import SimpleNamespace

    from app.agent.tools import _risk_action
    from app.services.live_agent_service import _knowledge_sources

    result = SimpleNamespace(
        id=7,
        rule_code="PRICE_MATCH",
        rule_name="计划到手价一致",
        severity="warning",
        sku_id=1,
        suggestion="先填写计划到手价",
        message="未设置计划到手价",
        evidence={},
    )
    action = _risk_action(result)
    assert action["action_type"] == "set_planned_price"
    assert action["requires_approval"] is False
    assert action["expected_value"] is None
    assert action["risk_id"] == 7

    prompt_rows, sources = _knowledge_sources({
        "reranked": True,
        "results": [{"score": 0.0002, "content": "无关 IT 制度"}],
    })
    assert prompt_rows == []
    assert sources == [], "低相关资料不能在前端显示为本场风险的知识依据"


def test_live_agent_without_inspection_skips_llm(client, make_user, monkeypatch):
    from app.services import live_agent_service

    admin = make_user("admin")
    session_id = _seed_session(client, admin, with_item=False)

    def must_not_call(*args, **kwargs):
        raise AssertionError("没有巡检事实时不应调用模型")

    monkeypatch.setattr(live_agent_service.llm, "chat", must_not_call)
    body = client.post(
        f"/api/agent/live-sessions/{session_id}/explain",
        json={"question": "现在能开播吗？"},
        headers=admin["headers"],
    ).json()

    assert body["code"] == 0
    data = body["data"]
    assert data["inspection_id"] is None
    assert "还没有巡检记录" in data["answer"]
    assert data["recommended_actions"][0]["action_type"] == "run_inspection"
    assert data["usage"] is None


def test_live_agent_respects_session_ownership(client, make_user, monkeypatch):
    owner = make_user("admin")
    outsider = make_user("employee")
    session_id = _seed_session(client, owner, with_item=False)

    body = client.post(
        f"/api/agent/live-sessions/{session_id}/explain",
        json={"question": "查看这场直播"},
        headers=outsider["headers"],
    ).json()

    assert body["code"] == 40400
    assert "不存在或没有访问权限" in body["message"]


def _price_risk(inspection: dict) -> dict:
    return next(
        row for row in inspection["data"]["results"]
        if row["rule_code"] == "PRICE_MATCH" and row["status"] == "failed"
    )


def test_agent_draft_and_approval_are_bound_to_inspection_risk(client, make_user):
    admin = make_user("admin")
    session_id = _seed_session(client, admin)
    inspection = client.post(
        f"/api/commerce/sessions/{session_id}/inspections",
        headers=admin["headers"],
    ).json()
    risk = _price_risk(inspection)

    draft_body = client.post(
        f"/api/agent/live-sessions/{session_id}/approval-draft",
        json={"risk_id": risk["id"]},
        headers=admin["headers"],
    ).json()
    assert draft_body["code"] == 0, draft_body
    draft = draft_body["data"]
    assert draft["inspection_id"] == inspection["data"]["id"]
    assert draft["source_inspection_result_id"] == risk["id"]
    assert draft["task_type"] == "price_fix"
    assert draft["expected_value"] == {"live_price": "99.00"}
    assert draft["can_submit"] is True

    approval_body = client.post(
        "/api/rpa/approvals",
        json={
            "session_id": draft["session_id"],
            "task_type": draft["task_type"],
            "sku_id": draft["sku_id"],
            "expected_value": draft["expected_value"],
            "reason": draft["reason"],
            "source_inspection_result_id": draft["source_inspection_result_id"],
        },
        headers=admin["headers"],
    ).json()
    assert approval_body["code"] == 0, approval_body
    approval = approval_body["data"]
    assert approval["source_inspection_result_id"] == risk["id"]
    assert approval["source_snapshot"]["inspection_id"] == inspection["data"]["id"]
    assert approval["source_snapshot"]["risk_id"] == risk["id"]
    assert approval["source_snapshot"]["evidence"]["planned_unit_price"] == "99.00"

    duplicate_draft = client.post(
        f"/api/agent/live-sessions/{session_id}/approval-draft",
        json={"risk_id": risk["id"]},
        headers=admin["headers"],
    ).json()["data"]
    assert duplicate_draft["can_submit"] is False
    assert duplicate_draft["existing_approval_id"] == approval["id"]


def test_risk_bound_approval_rejects_tampered_price(client, make_user):
    admin = make_user("admin")
    session_id = _seed_session(client, admin)
    inspection = client.post(
        f"/api/commerce/sessions/{session_id}/inspections",
        headers=admin["headers"],
    ).json()
    risk = _price_risk(inspection)
    draft = client.post(
        f"/api/agent/live-sessions/{session_id}/approval-draft",
        json={"risk_id": risk["id"]},
        headers=admin["headers"],
    ).json()["data"]

    body = client.post(
        "/api/rpa/approvals",
        json={
            "session_id": session_id,
            "task_type": "price_fix",
            "sku_id": draft["sku_id"],
            "expected_value": {"live_price": "88.00"},
            "source_inspection_result_id": risk["id"],
        },
        headers=admin["headers"],
    ).json()
    assert body["code"] == 40000
    assert "目标价格与最新巡检计划价不一致" in body["message"]


def test_old_inspection_risk_cannot_create_approval(client, make_user):
    admin = make_user("admin")
    session_id = _seed_session(client, admin)
    first = client.post(
        f"/api/commerce/sessions/{session_id}/inspections",
        headers=admin["headers"],
    ).json()
    old_risk = _price_risk(first)
    second = client.post(
        f"/api/commerce/sessions/{session_id}/inspections",
        headers=admin["headers"],
    ).json()
    current_risk = _price_risk(second)

    body = client.post(
        "/api/rpa/approvals",
        json={
            "session_id": session_id,
            "task_type": "price_fix",
            "sku_id": old_risk["sku_id"],
            "expected_value": {"live_price": "99.00"},
            "source_inspection_result_id": old_risk["id"],
        },
        headers=admin["headers"],
    ).json()
    assert old_risk["id"] != current_risk["id"]
    assert body["code"] == 40000
    assert "来源已失效" in body["message"]
