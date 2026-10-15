"""response_model 守卫：钉住"加了字段却忘了加 schema → 被静默丢掉"这一类错。

**为什么需要这个文件**：`response_model` 会按 schema 序列化输出，**没声明的字段
不会报错，而是直接消失**。本项目已经因为这个坑栽过一次（`GET /users` 那轮：
我一度给列表加了 email，撞了 `test_admin_lists_users_without_secrets` 才被发现），
所以每个补上 response_model 的模块都该有一道闸门。

`conversations` / `memories` 曾是全项目最后两个 0 声明的模块，也是形状最"松"的两个：
`MessageOut.meta` 是自由 dict、`sources` 是可变长列表 —— 最需要在加字段时被盯着。

**断言是双向的**：
  1. 响应里出现的每个键，都必须能在 schema 里找到声明
     （找不到 = 它正在被静默丢弃，或 schema 声明得不够）
  2. schema 里每个必填字段，都必须出现在响应里
     （没出现 = 声明了却产不出来，文档在撒谎）
另外直接钉 `/openapi.json` 里这 5 个端点的 response 声明本身，
防止有人把 `response_model=` 删掉而测试全绿（删了不会红，因为输出一个字都不变）。
"""

import pytest

from app.models import Conversation, Message
from app.schemas.conversation import ConversationDetail, ConversationItem, MessageOut
from app.schemas.memory import MemoryOut

# 一条"什么字段都有"的 assistant 消息：sources 覆盖 SourceItem 的全部字段、
# meta 放足够多的键。字段越全，越能在"有人删了 schema 里的字段"时立刻暴露。
_RICH_SOURCE = {
    "index": 1,
    "chunk_id": 101,
    "document_id": 11,
    "document_name": "考勤与休假制度.md",
    "knowledge_base_id": 1,
    "page_number": 3,
    "score": 0.87,
    "retrieval_path": "both",
    "snippet": "入职满一年可享受带薪年假 5 天。",
}
_RICH_META = {
    "answer_mode": "grounded",
    "hit": True,
    "reranked": True,
    "searched_kb_ids": [1],
    "skipped_orphans": 0,
    "model": "deepseek-flash",
    "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    "elapsed_ms": 1830,
    "finish_reason": "stop",
}


def _seed_conversation(session_factory, user_id: int) -> int:
    """造一段"一问一答 + 满字段引用与元信息"的会话，返回它的 id。"""
    with session_factory() as db:
        conv = Conversation(user_id=user_id, title="年假能休几天")
        db.add(conv)
        db.flush()
        db.add(Message(conversation_id=conv.id, role="user", content="年假能休几天"))
        db.add(
            Message(
                conversation_id=conv.id,
                role="assistant",
                content="入职满一年可享受带薪年假 5 天 [1]。",
                sources=[dict(_RICH_SOURCE)],
                meta=dict(_RICH_META),
            )
        )
        db.commit()
        return conv.id


def _assert_schema_matches(data: dict, model: type, where: str) -> None:
    """双向断言：响应的键 ↔ schema 的声明，谁多了谁少了都红。"""
    declared = set(model.model_fields)
    required = {n for n, f in model.model_fields.items() if f.is_required()}
    actual = set(data)

    extra = sorted(actual - declared)
    assert not extra, (
        f"{where} 返回了 schema 未声明的键 {extra} —— "
        "要么把它们加进 schema，要么说明它们正在被 response_model 静默丢掉"
    )
    missing = sorted(required - actual)
    assert not missing, (
        f"{where} 缺少 schema 里的必填字段 {missing} —— "
        "schema 声明了却产不出来，OpenAPI 文档在撒谎"
    )


# ==================== 声明本身还在不在 ====================


@pytest.mark.parametrize(
    "path,method,expect",
    [
        ("/api/conversations", "get", "ConversationItem"),
        ("/api/conversations/{conversation_id}", "get", "ConversationDetail"),
        ("/api/memories", "get", "MemoryOut"),
        ("/api/memories", "post", "MemoryOut"),
    ],
)
def test_endpoints_still_declare_response_model(client, path, method, expect):
    """这 4 个读/写端点的 response_model 声明必须存在。

    删掉 `response_model=` **不会让任何现有测试变红**（输出一个字节都不变），
    只会让 OpenAPI 文档悄悄退化。所以这里直接钉 `/openapi.json`。
    """
    spec = client.get("/openapi.json").json()
    op = spec["paths"][path][method]
    schema = op["responses"]["200"]["content"]["application/json"]["schema"]
    # 用 .get 而不是直接下标：声明被删掉时这里应当弹出**上面那句人话**，
    # 而不是一个看不出所以然的 KeyError。
    schema_ref = schema.get("$ref", "")
    assert expect in schema_ref, (
        f"{method.upper()} {path} 的 response 声明漂移了：$ref={schema_ref!r}，"
        f"期望包含 {expect}（是不是有人把 response_model 删了？）"
    )


# ==================== 满字段数据不能被悄悄丢掉 ====================


def test_conversation_list_keeps_every_field(client, make_user, session_factory):
    # ⚠️ 用**管理员**造会话：`get_accessible_kb_ids` 对管理员返回 None（不受限），
    # 引用不会被回放脱敏抹掉。用 employee 的话 allowed=[] → assistant 整条被脱敏，
    # 测的就不是 response_model 而是脱敏了（那条路另有专门的用例）。
    admin = make_user("admin")
    conv_id = _seed_conversation(session_factory, admin["id"])

    data = client.get("/api/conversations", headers=admin["headers"]).json()["data"]
    assert [c["id"] for c in data] == [conv_id]
    for item in data:
        _assert_schema_matches(item, ConversationItem, "GET /api/conversations")


def test_conversation_detail_keeps_every_field(client, make_user, session_factory):
    admin = make_user("admin")
    conv_id = _seed_conversation(session_factory, admin["id"])

    data = client.get(f"/api/conversations/{conv_id}", headers=admin["headers"]).json()["data"]
    _assert_schema_matches(data, ConversationDetail, "GET /api/conversations/{id}")

    assert data["has_more"] is False
    assert [m["role"] for m in data["messages"]] == ["user", "assistant"]
    for msg in data["messages"]:
        _assert_schema_matches(msg, MessageOut, "detail.messages[]")

    assistant = data["messages"][1]
    # sources：SourceItem 的每个字段都必须原样回来，一个都不能少
    assert assistant["sources"] == [_RICH_SOURCE]
    # meta：自由 dict，**任何键都不能丢**（它哪天被改成刚性模型，这条会红）
    assert assistant["meta"] == _RICH_META


def test_redacted_message_still_matches_schema(client, make_user, session_factory):
    """脱敏之后的输出**也必须**符合声明的 schema。

    这条守住的是另一条边界：脱敏会把 assistant 消息掰成
    `content=REDACTED_ANSWER / sources=[] / meta 只剩 source`。
    这个形状是**运行时才出现的**，如果 schema 这边把某个字段改成必填，
    脱敏路径就会在返回时才炸（500），而上面那条满字段用例永远绿。
    """
    employee = make_user()  # 没有任何授权 → allowed=[] → 引用的库全部"已撤销"
    conv_id = _seed_conversation(session_factory, employee["id"])

    data = client.get(
        f"/api/conversations/{conv_id}", headers=employee["headers"]
    ).json()["data"]
    for msg in data["messages"]:
        _assert_schema_matches(msg, MessageOut, "detail.messages[]（脱敏后）")

    assistant = data["messages"][1]
    assert assistant["sources"] == [], "引用应当被抹掉"
    assert assistant["meta"]["redacted"] is True
    # user 自己敲的字不受影响（口径：收资料，不收用户自己的话）
    assert data["messages"][0]["content"] == "年假能休几天"


def test_memories_keep_every_field(client, make_user):
    u = make_user()

    created = client.post(
        "/api/memories", json={"content": "我在财务部"}, headers=u["headers"]
    ).json()
    assert created["code"] == 0, created
    _assert_schema_matches(created["data"], MemoryOut, "POST /api/memories")

    listed = client.get("/api/memories", headers=u["headers"]).json()["data"]
    assert len(listed) == 1
    for item in listed:
        _assert_schema_matches(item, MemoryOut, "GET /api/memories")
    assert listed[0]["content"] == "我在财务部"
    # 刻意无外键的一列：没传 conversation_id 时必须是 null，不能被吞成字段缺失
    assert listed[0]["source_conversation_id"] is None
