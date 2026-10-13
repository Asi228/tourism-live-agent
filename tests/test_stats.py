"""概览统计（`GET /stats/overview`）的测试。

口径按**当前用户可见范围**：员工看到的只是他有权访问的那部分，管理员额外拿到
用户数与向量条数（非管理员那一段是 `null`，前端整块不渲染）。
"""

import pytest


# ==================== 概览统计 ====================


def test_overview_returns_expected_shape(client, make_user):
    admin = make_user("admin")
    r = client.get("/api/stats/overview", headers=admin["headers"])
    assert r.json()["code"] == 0
    d = r.json()["data"]
    assert set(d.keys()) == {
        "scope", "documents", "questions", "recent_documents", "recent_questions", "admin",
    }
    assert d["scope"]["unlimited"] is True, "管理员是不受限"
    assert d["admin"] is not None, "管理员能拿到系统级指标"


def test_overview_admin_block_is_null_for_employee(client, make_user):
    """员工拿到的 admin 段是 **null**（不是 0）—— 前端据此整块不渲染。"""
    emp = make_user("employee")
    r = client.get("/api/stats/overview", headers=emp["headers"])
    assert r.json()["code"] == 0
    d = r.json()["data"]
    assert d["admin"] is None
    assert d["scope"]["unlimited"] is False, "员工不是不受限"


def test_overview_counts_only_own_questions(client, make_user, monkeypatch):
    """问答数只算自己的：别人的提问量对他没有意义。"""
    from app.rag import llm as llm_module

    a = make_user("employee")
    b = make_user("employee")

    # 让问答不需要真模型：直接 stub chat
    monkeypatch.setattr(
        llm_module, "chat",
        lambda *args, **kwargs: {"answer": "答案", "usage": {"total_tokens": 1}, "finish_reason": "stop"},
    )

    client.post("/api/chat", json={"query": "甲的问题"}, headers=a["headers"])

    ra = client.get("/api/stats/overview", headers=a["headers"]).json()["data"]
    rb = client.get("/api/stats/overview", headers=b["headers"]).json()["data"]
    assert ra["questions"]["total"] == 1, "自己的提问要算进去"
    assert rb["questions"]["total"] == 0, "不能把别人的提问算到自己头上"


def test_overview_covers_granted_kb_with_documents(client, make_user, session_factory):
    """🔴 员工「有可访问的库」+「库里有文档」的概览 —— 补的是上面三条的假绿。

    为什么原来那三条抓不到问题：统计查询有两个分支，它们一个都没走到 ——

      · 管理员：`allowed is None` → 不套 `.where(...)`；而测试库里**一份文档都没有**
        → 拼 dict 的列表推导一次都不执行；
      · 员工：`make_user` 建的员工没有任何库授权 → `allowed == []` → 函数第一行
        就 `return []`，连 `.where(...)` 都到不了。

    于是 `Document.kb_id` 这种**字段名写错**（模型上叫 `knowledge_base_id`）能一路活到
    线上，而且概览是默认首页 —— 一登录就 500。这条用例把两个分支各走一遍。
    """
    from app.models.document import Document

    admin = make_user("admin")
    emp = make_user("employee")
    kb = client.post(
        "/api/knowledge", json={"name": "有文档的库"}, headers=admin["headers"]
    ).json()["data"]
    client.post(
        f"/api/knowledge/{kb['id']}/members",
        json={"user_id": emp["id"], "permission": "read"},
        headers=admin["headers"],
    )
    with session_factory() as db:
        db.add(Document(
            knowledge_base_id=kb["id"], uploader_id=admin["id"],
            filename="员工手册.txt", stored_name="uuid-handbook.txt",
            file_path="/tmp/uuid-handbook.txt", file_type="txt", file_size=1234,
            status="completed", chunk_count=7, char_count=456,
        ))
        db.commit()

    body = client.get("/api/stats/overview", headers=emp["headers"]).json()
    assert body["code"] == 0, f"员工概览应当可用，实际：{body}"
    data = body["data"]
    assert data["documents"]["total"] == 1, "按可见范围算：授权的那份要算进来"
    assert data["documents"]["by_status"] == {"completed": 1}
    assert data["recent_documents"][0]["knowledge_base_id"] == kb["id"]

    # 管理员是另一条分支（不受限 → 不套 where），也得单独过一遍
    admin_data = client.get("/api/stats/overview", headers=admin["headers"]).json()["data"]
    assert admin_data["documents"]["total"] == 1
    assert admin_data["recent_documents"][0]["filename"] == "员工手册.txt"

    # 没被授权的人看不到别人的库统计（口径仍然是"按可见范围"，不是全库）
    outsider = make_user("employee")
    out_data = client.get("/api/stats/overview", headers=outsider["headers"]).json()["data"]
    assert out_data["documents"]["total"] == 0, "无授权的用户不该看到任何文档统计"
    assert out_data["recent_documents"] == []


# ==================== 趋势数据（/stats/trends） ====================


def test_trends_zero_fills_empty_days(client, make_user):
    """🔴 折线必须**补零** —— 这是 `question_trend` 的核心约定。

    没有提问的日子也要出现在结果里（count=0），否则折线断格，
    看起来像"数据丢了"而不是"没人提问"。
    """
    admin = make_user("admin")
    r = client.get("/api/stats/trends", headers=admin["headers"])
    assert r.json()["code"] == 0
    d = r.json()["data"]
    assert d["days"] == 7
    assert len(d["question_trend"]) == 7, "补零后必须是完整 7 天，一个都不能少"
    assert all(p["count"] == 0 for p in d["question_trend"]), "新库里一个提问都没有"
    assert d["documents_by_kb"] == []


def test_trends_counts_questions_and_documents(client, make_user, monkeypatch, session_factory):
    """有数据时：今天的提问要落在最后一天、各库文档数按数量倒序。"""
    from app.models.document import Document
    from app.rag import llm as llm_module

    admin = make_user("admin")
    monkeypatch.setattr(
        llm_module, "chat",
        lambda *a, **k: {"answer": "答案", "usage": {"total_tokens": 1}, "finish_reason": "stop"},
    )
    client.post("/api/chat", json={"query": "年假几天"}, headers=admin["headers"])

    kb = client.post(
        "/api/knowledge", json={"name": "库A"}, headers=admin["headers"]
    ).json()["data"]
    with session_factory() as db:
        for i in range(2):
            db.add(Document(
                knowledge_base_id=kb["id"], uploader_id=admin["id"],
                filename=f"文件{i}.txt", stored_name=f"uuid-{i}.txt",
                file_path=f"/tmp/uuid-{i}.txt", file_type="txt", file_size=100,
                status="completed", chunk_count=1, char_count=10,
            ))
        db.commit()

    d = client.get("/api/stats/trends", headers=admin["headers"]).json()["data"]
    assert sum(p["count"] for p in d["question_trend"]) == 1, "今天的提问要算进来"
    assert d["question_trend"][-1]["count"] == 1, "最后一天是今天"
    assert d["question_trend"][0]["count"] == 0, "最早一天没有提问"
    assert d["documents_by_kb"][0]["knowledge_base_id"] == kb["id"]
    assert d["documents_by_kb"][0]["count"] == 2


def test_trends_respects_permission(client, make_user, session_factory):
    """⚠️ 无授权的员工：`allowed == []` → 文档分布必须是空，**不能退化成全量**。

    这是 `documents_by_kb` 的 `kb_ids == []` 分支 —— 把它当成 None 处理的话，
    无权用户会拿到全公司的库分布。
    """
    from app.models.document import Document

    admin = make_user("admin")
    emp = make_user("employee")
    kb = client.post(
        "/api/knowledge", json={"name": "别人的库"}, headers=admin["headers"]
    ).json()["data"]
    with session_factory() as db:
        db.add(Document(
            knowledge_base_id=kb["id"], uploader_id=admin["id"],
            filename="私密.txt", stored_name="uuid-secret.txt",
            file_path="/tmp/uuid-secret.txt", file_type="txt", file_size=100,
            status="completed", chunk_count=1, char_count=10,
        ))
        db.commit()

    d = client.get("/api/stats/trends", headers=emp["headers"]).json()["data"]
    assert d["documents_by_kb"] == [], "无授权的人不该看到任何库的文档分布"
    assert all(p["count"] == 0 for p in d["question_trend"]), "别人的提问也不算进来"


