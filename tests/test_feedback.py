"""答案反馈（Bad Case 闭环）。

数据直接用 `session_factory` 造，不走 HTTP 提问 —— 这一组测的是**反馈本身**，
不需要 LLM 替身（那套 fixture 在 test_chat.py / test_memories.py 里各有一份，
为一个不碰模型的模块再抄一遍不划算）。
"""

import pytest
from sqlalchemy import select

from app.core.response import ErrorCode
from app.models import ROLE_ASSISTANT, ROLE_USER, Conversation, Message, MessageFeedback

REASON_WRONG = "答案错误"


def _seed_assistant_message(session_factory, user_id, role=ROLE_ASSISTANT):
    """造一段会话 + 一条指定角色的消息，返回 (conversation_id, message_id)。"""
    with session_factory() as db:
        conv = Conversation(user_id=user_id, title="年假能休几天")
        db.add(conv)
        db.flush()
        msg = Message(conversation_id=conv.id, role=role, content="入职满一年可享 5 天")
        db.add(msg)
        db.commit()
        return conv.id, msg.id


def _rate(client, user, message_id, **payload):
    return client.post(
        f"/api/messages/{message_id}/feedback", json=payload, headers=user["headers"]
    ).json()


# ==================== 基本行为 ====================


def test_up_and_down_are_recorded(client, make_user, session_factory):
    u = make_user()
    _id = _seed_assistant_message(session_factory, u["id"])[1]

    assert _rate(client, u, _id, rating="up")["code"] == 0
    assert _rate(client, u, _id, rating="down", reason=REASON_WRONG)["code"] == 0

    with session_factory() as db:
        rows = list(db.scalars(select(MessageFeedback)))
    assert len(rows) == 1, "同一个人在同一条上只该有一条评价"
    assert rows[0].rating == "down"
    assert rows[0].reason == REASON_WRONG


def test_repeat_submission_updates_instead_of_erroring(client, make_user, session_factory):
    """改主意是常态 —— 重复提交要更新，不能报"你已经评价过了"。"""
    u = make_user()
    _id = _seed_assistant_message(session_factory, u["id"])[1]

    assert _rate(client, u, _id, rating="down", reason=REASON_WRONG)["code"] == 0
    res = _rate(client, u, _id, rating="up")
    assert res["code"] == 0, res
    assert res["data"]["rating"] == "up"
    # 🔴 好评带原因是矛盾数据 —— 应当被清掉，不能留着"有用 + 答案错误"
    assert res["data"]["reason"] is None

    with session_factory() as db:
        rows = list(db.scalars(select(MessageFeedback)))
    assert len(rows) == 1
    assert rows[0].rating == "up"


def test_get_returns_mine_and_null_when_absent(client, make_user, session_factory):
    u = make_user()
    _id = _seed_assistant_message(session_factory, u["id"])[1]

    before = client.get(f"/api/messages/{_id}/feedback", headers=u["headers"]).json()
    assert before["code"] == 0
    assert before["data"] is None, "没评价过应当是 null，不是报错"

    _rate(client, u, _id, rating="up")
    after = client.get(f"/api/messages/{_id}/feedback", headers=u["headers"]).json()
    assert after["data"]["rating"] == "up"


def test_others_feedback_is_not_visible(client, make_user, session_factory):
    """反馈是私密数据 —— 别人不该读到你打过分。"""
    a, b = make_user(), make_user()
    _id = _seed_assistant_message(session_factory, a["id"])[1]

    _rate(client, a, _id, rating="down", reason=REASON_WRONG)
    got = client.get(f"/api/messages/{_id}/feedback", headers=b["headers"]).json()
    # b 没有 a 那个会话的权限 → 404（连"这条消息存在"都不该确认）
    assert got["code"] == ErrorCode.NOT_FOUND, got


# ==================== 校验 ====================


def test_bad_rating_is_rejected(client, make_user, session_factory):
    u = make_user()
    _id = _seed_assistant_message(session_factory, u["id"])[1]

    res = _rate(client, u, _id, rating="maybe")
    assert res["code"] == ErrorCode.PARAM_ERROR, res


def test_bad_reason_is_rejected(client, make_user, session_factory):
    u = make_user()
    _id = _seed_assistant_message(session_factory, u["id"])[1]

    res = _rate(client, u, _id, rating="down", reason="因为不开心")
    assert res["code"] == ErrorCode.PARAM_ERROR, res


def test_too_long_comment_is_rejected(client, make_user, session_factory):
    u = make_user()
    _id = _seed_assistant_message(session_factory, u["id"])[1]

    res = _rate(client, u, _id, rating="down", reason=REASON_WRONG, comment="长" * 501)
    assert res["code"] == ErrorCode.PARAM_ERROR, res


def test_cannot_rate_a_user_message(client, make_user, session_factory):
    """给自己的提问打"答案错误"没有意义 —— 只能评价助手的回答。"""
    u = make_user()
    _id = _seed_assistant_message(session_factory, u["id"], role=ROLE_USER)[1]

    res = _rate(client, u, _id, rating="down", reason=REASON_WRONG)
    assert res["code"] == ErrorCode.PARAM_ERROR, res


def test_unknown_message_is_not_found(client, make_user):
    u = make_user()
    assert _rate(client, u, 999999, rating="up")["code"] == ErrorCode.NOT_FOUND


def test_other_users_message_is_not_found(client, make_user, session_factory):
    """别人的消息一律 404 —— 403 等于白送"这个 id 存在"。"""
    a, b = make_user(), make_user()
    _id = _seed_assistant_message(session_factory, b["id"])[1]

    res = _rate(client, a, _id, rating="up")
    assert res["code"] == ErrorCode.NOT_FOUND, res
    # 对方的数据没被动过
    with session_factory() as db:
        assert db.scalar(select(MessageFeedback)) is None


def test_endpoints_require_auth(client):
    assert client.post("/api/messages/1/feedback", json={"rating": "up"}).json()[
        "code"
    ] == ErrorCode.UNAUTHORIZED
    assert client.get("/api/messages/1/feedback").json()["code"] == ErrorCode.UNAUTHORIZED


# ==================== 级联 ====================


@pytest.mark.parametrize("role", [ROLE_ASSISTANT, ROLE_USER])
def test_feedback_model_defines_expected_columns(role):
    """常量与列定义钉一下，防止改模型时静默漏掉（反馈表是新的，没有别的守卫）。"""
    assert role in ("user", "assistant")
    cols = set(MessageFeedback.__table__.columns.keys())
    assert {
        "id", "message_id", "user_id", "rating", "reason",
        "comment", "created_at", "updated_at",
    } <= cols, cols
